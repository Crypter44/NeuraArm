# High-Level Admittance Control for Whiteboard Drawing

## Problem and proposed approach

The drawing application currently follows precomputed TCP poses. It relies on
manual calibration because surface height and tracing/position errors cause the
pen force to vary over the whiteboard. Add a high-level, normal-axis admittance
loop that preserves the planned XY trajectory while adapting the TCP position
normal to the board from measured force.

The initial implementation should be limited to drawing segments. Travel and
pen-up motion remain unchanged. Contact transitions use a configurable contact
threshold and a lower release threshold (hysteresis). The controller must be
conservative against stiff surfaces: generous damping, low gain, bounded
correction, bounded correction speed, and short command intervals.

## Repository findings

- [`robot_draw.py`](/home/daniel/projects/robotum/NeuraArm/robot_draw.py)
  owns robot connection, mode switching, motion execution, and the current
  `Robot.move_linear(...)` calls.
- [`pdraw/robot.py`](/home/daniel/projects/robotum/NeuraArm/pdraw/robot.py)
  generates pure planned poses. `PaperFrame` contains the calibrated paper
  axes and normal; `plan()` produces draw and travel moves.
- [`robot_calibrate.py`](/home/daniel/projects/robotum/NeuraArm/robot_calibrate.py)
  records paper origin, axes, and normal. The normal can be tilted relative to
  robot-base Z when a paper calibration is supplied.
- [`robot_readtest.py`](/home/daniel/projects/robotum/NeuraArm/robot_readtest.py)
  is the existing read-only robot capability probe.
- [`vendor/neurapy/robot.py`](/home/daniel/projects/robotum/NeuraArm/vendor/neurapy/robot.py)
  is a dynamic socket client. It exposes robot-controller methods returned by
  `get_functions()` and contains no force, torque, wrench, impedance, or
  admittance method in the checked-in source.
- No force/torque reader is currently referenced anywhere in the repository.
  The runtime/controller API must therefore be established before relying on
  physical feedback.

## Unresolved force API requirements

Before implementation or hardware testing, determine from the robot controller's
`get_functions()` output or controller documentation:

1. Force/torque read method name.
2. Returned vector ordering (for example, force XYZ followed by torque XYZ).
3. Units.
4. Coordinate frame (base, TCP, tool, or other).
5. Sign convention for force normal to the board.
6. Update rate and communication latency.
7. Availability and behavior in simulation.

Runtime capability discovery may identify candidate methods, but ambiguous
discovery must require an explicit configured method and wrench mapping. A real
robot run must fail explicitly when no usable force reader is available; it must
not silently proceed without feedback.

## Controller design

### Force signal

- Read the configured force/torque vector each control interval.
- Project onto the calibrated paper normal. The current simplified hardware
  assumption is that robot Z is always the board normal, but implementation
  should use the paper-frame normal when calibration supplies a tilted plane.
- Ignore tangential force components so pen drag does not change the normal
  correction.
- Establish and document the sign convention and target-force interpretation.
- Support a small configurable deadband and/or low-pass filter if needed for
  measured noise.

### Admittance state

Use a conservative first-order or mass-damper normal admittance model. The
controller should expose a deterministic update function suitable for synthetic
force tests. Each update should:

1. Compute force error against the configured target.
2. Apply low gain and generous damping.
3. Integrate/update the normal displacement.
4. Clamp displacement to a hard bounded range (nominally ±5 mm).
5. Clamp correction velocity and per-cycle position increment.

Hard safety limits must be enforced in code even if configuration values are
more permissive.

### Contact transitions

Use a state machine with at least `approaching`, `contact/regulating`, and
`lifting/released` behavior:

- Approach each drawing segment using the nominal planned position.
- Enter force regulation after normal force crosses the configurable contact
  threshold.
- Use a lower release threshold to avoid chatter.
- On lift-off, stop regulation and return to nominal planned normal position.
- Add configurable contact and release timeouts.
- On timeout, invalid force data, unsupported API, or communication failure,
  stop/abort explicitly according to repository-standard robot stop behavior.

### Online motion adaptation

The existing optimized path sends a whole draw primitive as one blended
`move_linear` call. That does not provide feedback opportunities. Regulated
drawing must instead use short command intervals:

1. Read the current normal force.
2. Update the controller state.
3. Add the bounded normal correction to the next nominal pose.
4. Issue a short linear motion while retaining nominal XY and TCP
   orientation.

The correction must be applied along the calibrated paper normal, not blindly
along base Z for tilted paper. Travel and pen-up moves retain current behavior.

## Configuration

Add a configuration file under `config/`, expected to be
`config/admittance.json`, containing validated values for:

- enable flag and force-reader method/mapping;
- target normal force;
- contact and release thresholds;
- admittance gain and damping;
- filter/deadband settings;
- control interval and short command duration;
- maximum normal correction;
- maximum correction velocity and per-cycle increment;
- contact and release timeouts;
- optional simulation/test behavior.

Configuration loading and validation should reject missing, non-finite,
negative, contradictory, or unsafe values with clear errors. CLI overrides may
be added only where useful for controlled experiments; the file remains the
source for controller gains and safety parameters.

## Simulation and tests

The selected simulation policy is: disable physical feedback in `--sim`, but
test the controller with synthetic force inputs. Do not claim that current
simulation validates hardware force behavior.

Add unit tests for:

- normal-force projection and sign handling;
- target-force error and controller update;
- displacement, velocity, and per-cycle bounds;
- contact/release hysteresis;
- contact/release timeout and invalid-input failures;
- preservation of nominal XY path and TCP orientation;
- correction along a tilted calibrated paper normal;
- configuration validation;
- synthetic force sequences in simulation/test mode.

Use the existing test style and geometry coverage in
[`tests/test_robot.py`](/home/daniel/projects/robotum/NeuraArm/tests/test_robot.py).

## Documentation and operational safety

Update [`README.md`](/home/daniel/projects/robotum/NeuraArm/README.md) with:

- configuration location and parameters;
- force API prerequisites and units/frame assumptions;
- contact-state behavior;
- simulation limitations;
- bounded correction and emergency-stop requirements.

Once the API is known, extend
[`robot_readtest.py`](/home/daniel/projects/robotum/NeuraArm/robot_readtest.py)
with a read-only force capability/value probe. Hardware validation should be
performed incrementally with low gains, generous damping, small speed, and the
E-stop accessible.

## Todos

1. Establish and validate the runtime force/torque API and coordinate/sign
   conventions.
2. Design and implement the bounded normal admittance state machine.
3. Add validated `config/admittance.json` loading and safety limits.
4. Integrate short-interval feedback motion into drawing segments only.
5. Add synthetic-force, geometry, transition, timeout, and configuration tests.
6. Update the read-only probe and README documentation.
7. Run focused tests and perform simulation/hardware-readiness verification.

## Notes

- Do not remove the existing calibration workflow yet. Admittance should
  compensate force variations but calibration remains useful for plane geometry
  and safe nominal positioning.
- Do not react to raw wrench magnitude because tangential pen friction is
  expected.
- Do not silently fall back to open-loop drawing on a real arm when feedback is
  unavailable.
- Avoid broad changes to tracing or pure drawing geometry.
