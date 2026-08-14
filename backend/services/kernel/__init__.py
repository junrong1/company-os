"""The kernel service: sole log writer, owner of the clock.

Runs at exactly one instance (R1). At this unit that is asserted by compose
refusing to scale a service with a fixed container name; from U6 it is enforced at
the store by a writer lease, because a compose replica cap is a setting and not a
mechanism.
"""
