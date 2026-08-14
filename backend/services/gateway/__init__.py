"""The gateway service: the client's only contact surface.

Commands in over REST, events out over WebSocket (U10). It holds no simulation
state and no write handle to the log.
"""
