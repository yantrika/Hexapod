"""Message contract between voice/brain and the body process.

Defines the multiprocessing queues and the typed message schema, e.g.
``{"action": "walk", "direction": "fwd", "speed": 0.5}``. Voice and brain code
send these messages and never touch joint angles.
"""
