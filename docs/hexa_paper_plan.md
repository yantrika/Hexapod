# HEXA Research Paper Plan — Directions 2 and 3

## One-line idea
Make a cheap hexapod (MG995 servos, no joint feedback, Raspberry Pi 5) walk with a policy trained in simulation, by first measuring and modelling how cheap servos really behave.

## Why this is worth a paper
- Sim-to-real (train in simulation, run on a real robot) is still an open problem. There is no agreed way to choose what to randomize in simulation, and most success stories use expensive robots with good motors and powerful onboard computers.
- Recent cheap hexapods use better hardware than ours:
  - Spiderbot (2026, under $400) uses serial servos that report their position. Its walking speed reached only about half of the simulation value, and the authors list better actuator modelling as future work.
  - SpiderPi (Berkeley, about $600) adds depth and tracking cameras.
- Nobody we found has done it with **analog hobby servos (MG995), no joint feedback, only an IMU, on a Pi 5**. That is our gap.

## The paper = two parts that build on each other

### Part A (Direction 2): Measure the cheap servo
Question: *What does an MG995 actually do, and which of its flaws must the simulator include for the robot to behave as predicted?*

Measure on a test bench, one servo:
- Response delay (command sent to movement start)
- Deadband (smallest command that moves it)
- Real speed under load vs the datasheet
- Torque drop as battery voltage sags
- Unit-to-unit variation (test all 18–22 servos)

How to read the real angle (MG995 has no feedback):
- Option 1: solder a wire to the servo's internal potentiometer and read it with an ADC (for example ADS1115 on the Pi's I2C)
- Option 2: coloured markers on the leg and a camera

Output: a small **servo model** (delay + deadband + speed limit + torque curve) put into the simulator.

Experiment: run our existing tripod gait (scripted, no learning) in simulation **with and without** the servo model, and on the real robot. Report how close each sim version gets to reality (distance walked, heading drift, body tilt, foot slip).

### Part B (Direction 3): Learn to walk with RL
Question: *Can a policy trained in simulation with our servo model walk on the real MG995 hexapod better than the scripted gait?*

- Train with PPO (standard RL algorithm) in a GPU simulator on the RTX 3060
- Policy input: IMU (body tilt and turn rate), previous actions, the velocity command
- Policy output: 18 joint targets, sent through our existing safety clamp and speed limit
- Domain randomization: vary mass, friction, and the servo model parameters from Part A
- Run the trained policy on the Pi 5 (small network, CPU is enough)

Compare (the main results table):

| Controller | Trained with |
| --- | --- |
| Scripted tripod gait | — (baseline) |
| RL policy | plain simulator |
| RL policy | + servo model (Part A) |
| RL policy | + servo model + randomization |

Metrics: speed reached vs commanded, heading drift, falls, body tilt, energy (battery current), servo temperature, and the sim-to-real ratio for each.

## What we already have (from HEXA)
- Robot model, kinematics, tripod gait, controller with safety clamps
- Simulator setup (PyBullet) and measurement scripts
- Pi 5 software stack, dry-run backend, web control page
- Tested latency and timing numbers on the Pi

## What we still need
- Built robot with 18 MG995 (buy spares), 2× PCA9685, 6 V high-current supply, kill switch
- An IMU on the Pi (for example MPU6050 or BNO055)
- Angle measurement for the servo bench (ADC or camera)
- A GPU simulator on the RTX 3060 (Isaac Lab or a MuJoCo-based one; check its requirements first)
- Battery current sensor (for energy), optional but useful

## Order of work
1. Build and calibrate the robot; tripod gait walking on the floor
2. Servo bench tests (Part A data)
3. Servo model in the simulator; sim vs real comparison with the scripted gait
4. RL training on the 3060, step by step (stand, then walk, then turn)
5. Real-robot RL tests with someone at the kill switch
6. Write up

Part A alone is a complete small paper if Part B runs out of time.

## Risks
- MG995 may be too weak for our leg length: check torque before cutting the frame
- RL policies can shake servos: limit action changes per step and keep the speed limit
- Reading the servo pot needs opening a servo: practise on a spare

## Key references
- The Reality Gap in Robotics (survey): https://arxiv.org/html/2510.20808v1
- Sim-to-Real for Legged Robots (survey thesis): https://hulks.de/_files/PA_Luis-Scheuch.pdf
- Spiderbot, low-cost hexapod with RL: https://arxiv.org/html/2609.26989v1
- Versatile Locomotion Skills for Hexapod Robots (SpiderPi): https://arxiv.org/html/2412.10628v1
