# -*- coding: utf-8 -*-
"""
PiHexa Single Leg Web Controller & Serial Bridge for Arduino Mega 2560
"""

import os
import sys
import time
import threading
from flask import Flask, jsonify, request, send_from_directory

# Add pihexa folder to sys.path so we can reuse all verified kinematics
current_dir = os.path.dirname(os.path.abspath(__file__))
pihexa_dir = os.path.abspath(os.path.join(current_dir, '..', 'pihexa'))
if pihexa_dir not in sys.path:
    sys.path.append(pihexa_dir)

from movement import Movement, MovementMode
from leg import BaseLeg
from config import movement_switch_duration
import serial
import serial.tools.list_ports

app = Flask(__name__, static_folder='static', static_url_path='')

class LegController:
    def __init__(self):
        self.ser = None
        self.port = None
        self.baudrate = 115200
        self.is_connected = False

        # Leg & Motion configuration
        self.selected_leg_id = 0  # 0 to 5
        self.legs = [BaseLeg(i) for i in range(6)]
        self.movement = Movement(MovementMode.MOVEMENT_STANDBY.value, False)
        self.current_mode = MovementMode.MOVEMENT_STANDBY.value
        self.mode_label = "Standby"

        # Walking cycle thread state
        self.is_walking = False
        self.cycle_interval_ms = 35.0  # control cycle interval in ms
        self.current_angles = [0.0, 0.0, 0.0]
        self.current_tip = [0.0, 0.0, 0.0]
        self.trim_offsets = [0.0, 0.0, 0.0]

        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def get_available_ports(self):
        ports = serial.tools.list_ports.comports()
        return [{"device": p.device, "description": p.description} for p in ports]

    def connect(self, port, baudrate=115200):
        with self.lock:
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except Exception:
                    pass
            try:
                self.ser = serial.Serial(port, baudrate, timeout=0.1)
                self.port = port
                self.baudrate = baudrate
                self.is_connected = True
                time.sleep(1.8)  # Allow Arduino Mega reset sequence to complete
                self.ser.reset_input_buffer()
                self._send_command("PING\n")
                return True, f"Connected to {port}"
            except Exception as e:
                self.is_connected = False
                return False, str(e)

    def disconnect(self):
        with self.lock:
            self.is_walking = False
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except Exception:
                    pass
            self.is_connected = False
            self.ser = None
            return True

    def set_leg(self, leg_id):
        with self.lock:
            if 0 <= leg_id < 6:
                self.selected_leg_id = leg_id
                self._update_current_leg_angles()
                return True, f"Switched to Leg {leg_id}"
            return False, "Invalid leg id (0-5)"

    def set_mode(self, mode_str):
        with self.lock:
            try:
                val = MovementMode.get_value(mode_str)
                self.current_mode = val
                self.mode_label = mode_str
                self.movement.set_mode(val)
                return True, f"Mode set to {mode_str}"
            except Exception as e:
                return False, f"Unknown mode: {mode_str}"

    def set_walking(self, active: bool):
        with self.lock:
            self.is_walking = active
            return True

    def set_speed(self, interval_ms: float):
        with self.lock:
            self.cycle_interval_ms = max(10.0, min(150.0, interval_ms))
            return True

    def set_manual_angles(self, coxa, femur, tibia):
        with self.lock:
            self.is_walking = False
            self.current_angles = [float(coxa), float(femur), float(tibia)]
            cmd = f"A {self.current_angles[0]:.2f} {self.current_angles[1]:.2f} {self.current_angles[2]:.2f}\n"
            self._send_command(cmd)
            return True

    def set_trim_offsets(self, coxa_trim, femur_trim, tibia_trim):
        with self.lock:
            self.trim_offsets = [float(coxa_trim), float(femur_trim), float(tibia_trim)]
            cmd = f"OFFSET {self.trim_offsets[0]:.2f} {self.trim_offsets[1]:.2f} {self.trim_offsets[2]:.2f}\n"
            self._send_command(cmd)
            return True

    def _update_current_leg_angles(self):
        target_world_loc = self.movement.next(elapsed=0).get(self.selected_leg_id)
        local_dest = self.legs[self.selected_leg_id].translate2local(target_world_loc)
        angles = BaseLeg.inverse_kinematics(local_dest)
        self.current_angles = [round(a, 2) for a in angles]
        self.current_tip = [round(local_dest.x, 2), round(local_dest.y, 2), round(local_dest.z, 2)]

    def _send_command(self, cmd_str):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(cmd_str.encode('utf-8'))
                self.ser.flush()
                print(f"[Serial TX] -> {cmd_str.strip()}")
            except Exception as e:
                print(f"[Serial write error]: {e}")
        else:
            print(f"[Serial not open] Cannot send: {cmd_str.strip()}")

    def _loop(self):
        last_time = time.time()
        while True:
            cycle_time = self.cycle_interval_ms * 0.001
            now = time.time()
            elapsed_dt = now - last_time
            last_time = now

            if self.is_walking:
                with self.lock:
                    loc = self.movement.next(elapsed=int(self.cycle_interval_ms))
                    target_world_pt = loc.get(self.selected_leg_id)
                    local_pt = self.legs[self.selected_leg_id].translate2local(target_world_pt)
                    angles = BaseLeg.inverse_kinematics(local_pt)
                    self.current_angles = [round(angles[0], 2), round(angles[1], 2), round(angles[2], 2)]
                    self.current_tip = [round(local_pt.x, 2), round(local_pt.y, 2), round(local_pt.z, 2)]

                    cmd = f"A {self.current_angles[0]:.2f} {self.current_angles[1]:.2f} {self.current_angles[2]:.2f}\n"
                    self._send_command(cmd)

            # Sleep remainder of cycle
            spent = time.time() - now
            rem = cycle_time - spent
            if rem > 0:
                time.sleep(rem)
            else:
                time.sleep(0.002)

controller = LegController()

# --- ROUTES ---

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        "connected": controller.is_connected,
        "port": controller.port,
        "selected_leg": controller.selected_leg_id,
        "mode": controller.mode_label,
        "is_walking": controller.is_walking,
        "interval_ms": controller.cycle_interval_ms,
        "angles": controller.current_angles,
        "tip_local": controller.current_tip,
        "trim": controller.trim_offsets
    })

@app.route('/api/ports', methods=['GET'])
def list_ports():
    return jsonify(controller.get_available_ports())

@app.route('/api/connect', methods=['POST'])
def connect():
    data = request.json or {}
    port = data.get('port')
    baud = int(data.get('baud', 115200))
    if not port:
        return jsonify({"success": False, "error": "Port is required"}), 400
    ok, msg = controller.connect(port, baud)
    return jsonify({"success": ok, "message": msg})

@app.route('/api/disconnect', methods=['POST'])
def disconnect():
    controller.disconnect()
    return jsonify({"success": True})

@app.route('/api/leg', methods=['POST'])
def select_leg():
    data = request.json or {}
    leg_id = int(data.get('leg_id', 0))
    ok, msg = controller.set_leg(leg_id)
    return jsonify({"success": ok, "message": msg})

@app.route('/api/mode', methods=['POST'])
def select_mode():
    data = request.json or {}
    mode = data.get('mode', 'Standby')
    ok, msg = controller.set_mode(mode)
    return jsonify({"success": ok, "message": msg})

@app.route('/api/walking', methods=['POST'])
def toggle_walking():
    data = request.json or {}
    active = bool(data.get('active', True))
    controller.set_walking(active)
    return jsonify({"success": True, "is_walking": controller.is_walking})

@app.route('/api/speed', methods=['POST'])
def update_speed():
    data = request.json or {}
    interval_ms = float(data.get('interval_ms', 35.0))
    controller.set_speed(interval_ms)
    return jsonify({"success": True, "interval_ms": controller.cycle_interval_ms})

@app.route('/api/manual', methods=['POST'])
def manual_move():
    data = request.json or {}
    coxa = float(data.get('coxa', 0.0))
    femur = float(data.get('femur', 0.0))
    tibia = float(data.get('tibia', 0.0))
    controller.set_manual_angles(coxa, femur, tibia)
    return jsonify({"success": True})

@app.route('/api/trim', methods=['POST'])
def update_trim():
    data = request.json or {}
    c = float(data.get('coxa', 0.0))
    f = float(data.get('femur', 0.0))
    t = float(data.get('tibia', 0.0))
    controller.set_trim_offsets(c, f, t)
    return jsonify({"success": True})

if __name__ == '__main__':
    print("Starting PiHexa Single Leg Web Controller on http://localhost:5000 ...")
    app.run(host='0.0.0.0', port=5000, debug=False)
