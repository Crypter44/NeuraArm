# vendor/

`neurapy/robot.py` is NEURA Robotics' NeuraPy socket client (v5.0.20), copied from the
robot PC (`~/backup/software/temp/Neurapy-windows/robot.py`). It talks JSON over TCP to the
`start_neurapy_socket_interface` server on the robot PC (192.168.2.13:65432).

Proprietary NEURA software: do not publish this folder.

Local patches (marked `# local patch`):
- macOS is treated like Linux (the original imports `win32api` on every non-Linux OS).
- `prettytable` is optional; it is only used by `list_methods()`.
