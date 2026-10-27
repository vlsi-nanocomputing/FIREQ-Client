"""Configure the system from Python, then hand the same connection to the interactive prompt."""

from FIREQ_CLIENT import FireqClient

with FireqClient("0.0.0.0", 5000) as fq:
    fq.reset_all()
    fq.set_nyquist(228, 0, 2)
    fq.mts_sync()
    # returns on `quit`; the connection stays open and is closed by the with block
    fq.start_interactive()
