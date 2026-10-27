"""Analyse the points while the sweep runs, and stop early once the signal is found."""

from FIREQ_CLIENT import FireqClient

THRESHOLD = 500.0

with FireqClient("0.0.0.0", 5000) as fq:
    fq.reset_all()
    # leaving the with block before the end closes the connection, which stops the sweep (v0 protocol)
    with fq.run_iter("yaml_experiment_configurations_examples/Rabi.yaml") as points:
        for point in points:
            magnitude = abs(next(iter(point.data.values()))["value"].mean())
            print(point.coordinates, magnitude)
            if magnitude > THRESHOLD:
                print("signal found, stopping")
                break
    print(f"{points.result.points_completed} points acquired")
