# `config.py`: a map of the settings

Every number in the project lives in `config.py`. Nothing else should hard-code a value. This page says what each group is for. Units: metres and radians, unless a name ends in `_DEG` (degrees), `_S` (seconds), `_HZ`, `_M`.

Values marked PLACEHOLDER in the file are guesses to tune. Settings that take effect only in later steps are marked *(later)*.

| Section in the file | Main settings | Change it to... |
|---|---|---|
| **Filesystem** | `ASSETS_DIR`, `URDF_PATH`, `VOSK_DIR`, `PIPER_DIR`, `LOG_DIR`, `LOG_LEVEL` | Move where models and logs are stored. |
| **Body geometry** | `BODY_RADIUS`, `COXA_LENGTH`, `FEMUR_LENGTH`, `TIBIA_LENGTH`, `LEG_NAMES`, `LEG_MOUNT_ANGLES_DEG`, `TRIPOD_A/B` | Match a different robot size. Then run `scripts/generate_urdf.py`. |
| **Joint limits** | `JOINT_HARD_LIMITS_DEG` (servo range, ±90), `GAIT_SOFT_LIMITS_DEG` (walking range) | Protect the servos / stop legs colliding. Soft must stay inside hard. |
| **Motion clamps** | `SPEED_MAX`, `TURN_*`, `STEP_LENGTH_MAX_M`, `STEP_HEIGHT_M`, `BODY_HEIGHT_SIT`, `FALL_TILT_DEG` | Limit speed, stride, turn size, when it counts as fallen. |
| **Simulation model** | `BODY_MASS_KG`, `LINK_MASS_KG`, `JOINT_MAX_FORCE_NM`, `SIM_GRAVITY`, friction, gains | Make the simulation closer to the real robot. Simulation only. |
| **Controller** | `VELOCITY_RAMP_S`, `SIT_STAND_TRANSITION_S`, `SETTLE_S`, `WAVE_*`, `FOOT_TARGET_MAX_SPEED_M_S` | Change how smooth or fast postures and the wave are. |
| **Manual control** | `TELEOP_SPEED_SCALE_*` | Keyboard driving speed steps. |
| **Timing and bridge** | `PHYSICS_HZ` (240), `CONTROL_HZ` (50), `GAIT_PERIOD_S`, `MAX_MESSAGE_AGE_S`, `WATCHDOG_TIMEOUT_S` (1 s), `HEARTBEAT_HZ` (5), queue sizes, `GUI_*` camera, `SIM_HEADLESS` | Change loop speed, safety timing, the viewer. |
| **Router** | `ROUTER_PHRASES`, `ROUTER_ALIASES`, `STOP_WORDS`, `ROUTER_FILLERS`, `ROUTER_THRESHOLD`, `ROUTER_MAX_WORDS` | Change the words the robot understands. |
| **Brain** | `VOICE_WALK_MAX_S`, `DIALOGUE_SPEAK_DONE`, `DIALOGUE_THROTTLE_S`, `VOICE_PUMP_S`, status queue sizes | How long a voiced walk is kept alive; whether "Done." is spoken; how often the same phrase may repeat. |
| **Audio** | `AUDIO_SAMPLE_RATE` (16000), `AUDIO_BLOCKSIZE`, `MIC_DEVICE`, `SPEAKER_DEVICE`, `SPEAK_TAIL_S`, `TTS_*` timeouts and queue sizes, `PHRASES_DIR`, `TTS_PHRASES` | Pick audio devices, change what is pre-recorded, change speech timeouts. |
| **Models** | `VOSK_MODELS`, `VOSK_MODEL_DEFAULT`, `STT_*` thresholds, `STT_USE_GRAMMAR`, `PIPER_BINARY`, `PIPER_VOICE`, `PIPER_MODEL_PATH`, `PIPER_NICE`, `PIPER_CPU_LIST`, `TRANSCRIPT_LOG` | Change a model; tune recognition. |
| **Chat** | `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_S`, `OLLAMA_KEEP_ALIVE`, `CHAT_SYSTEM_PROMPT`, `CHAT_MAX_TOKENS`, `CHAT_TEMPERATURE`, `CHAT_HISTORY_TURNS`, `CHAT_MAX_SENTENCE_CHARS` | Change the chat model, the personality, reply length, memory. |
| **Test tolerances** | `WALK_TEST_*`, `HOLD_TEST_*`, `BODY_TEST_*`, `IK_TOLERANCE_M` | How strict the simulation tests are. Not used by the robot. |

## The settings you are most likely to change

| Want to... | Setting |
|---|---|
| Replace the speech model | the name in `VOSK_MODELS` / `VOSK_MODEL_DEFAULT`, then `scripts/fetch_models.sh` |
| Replace the chat model | `OLLAMA_MODEL`, then `docker exec -it ollama ollama pull <name>` |
| Change the personality | `CHAT_SYSTEM_PROMPT` |
| Replace the speaking voice | `PIPER_VOICE`, then `scripts/fetch_models.sh` |
| Make "stop" easier or harder to trigger from the grammar | `STT_STOP_CONF` (lower = easier) |
| Make grammar commands easier or harder | `STT_GRAMMAR_CONF` (lower = easier, riskier) |
| Turn the command grammar off | `STT_USE_GRAMMAR = False` |
| Add or remove a spoken command | `ROUTER_PHRASES` |
| Add a stop word | `STOP_WORDS` |
| How long the robot keeps walking after a spoken "walk" | `VOICE_WALK_MAX_S` |
| How long the robot ignores the mic after it speaks | `SPEAK_TAIL_S` |
| Robot stops sooner if the brain stalls | `WATCHDOG_TIMEOUT_S` |
| Walk faster | `STEP_LENGTH_MAX_M`, `GAIT_PERIOD_S` |
| Pick a microphone | `MIC_DEVICE` |

## Two rules

- Never hard-code a value that belongs here. Add it here and import it.
- Bound values with `config.clamp(value, low, high)`.
