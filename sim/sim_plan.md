# Drone inspection simulation — initial implementation plan

## Objective

Build an interactive indoor drone inspection prototype using existing HM3D/MP3D GLB scenes. Support autonomous coverage and user-directed inspection, live drone views, physical flight control, and visible inspection progress.

## Assumptions and initial scope

- `habitat_sim` is already installed. Verify its renderer and scene loading before implementation.
- Scene paths are placeholders: `assets/scenes/<scene_name>.glb`.
- Starting drone position and orientation are supplied by localization; localization itself is outside the first version.
- Start with one static scene and one drone. Use mesh rendering first; keep a renderer interface for a later 3DGS backend.
- Use an existing quadrotor URDF and compatible flight controller. Pin its source, license, version, geometry scale, mass, inertia, motor locations, and actuator parameters before integration. Placeholder: `assets/drone/<quadrotor>.urdf`.
- The first version measures geometric inspection coverage. It does not claim to detect real defects or reproduce real-world flight faithfully.

## Architecture and physics integration

Use Habitat-Sim for scene rendering and camera observations. Use a dedicated flight-physics backend, initially PyBullet with a compatible existing quadrotor controller, to load the URDF and simulate motor forces and torques. Confirm package compatibility in an initial integration spike.

A URDF describes the robot; it does not supply a flight controller. Do not assume Habitat-Sim can directly load and fly the selected URDF. The proposed bridge gives the flight backend ownership of the drone state and mirrors that state into Habitat for rendering.

Load aligned static collision geometry derived from the GLB into the physics backend. Preserve transforms and units, bake scene-node transforms where necessary, and validate mesh holes and simplified collision geometry. Use one documented world coordinate convention and explicit conversions for Habitat, physics, camera, and UI frames.

Data flow:

```text
UI task / chat / selected target / velocity command
                    ↓
Task manager → inspection planner → trajectory / velocity controller
                    ↓
Flight controller → motor forces → physics → timestamped drone pose
                                             ↓
                            Habitat camera + map + coverage evaluator
                                             ↓
                                 UI observations and progress
```

Run physics at a fixed timestep; render and refresh the UI independently. Timestamp observations and evaluate coverage using the camera pose at capture time. The UI should remain responsive when an agent request is slow.

## User-settable velocity and camera parameters

Expose `vel_cmd = [vx, vy, vz, yaw_rate]`, with linear velocity in m/s and yaw rate in rad/s. Default to body-frame horizontal commands and world-up vertical velocity; label this explicitly. Offer a world-frame option only if the conversion is implemented and tested.

- Controls: numeric fields, sliders, keyboard input, hover, pause, reset, and emergency stop.
- Route manual and autonomous velocity commands through the same controller and limits.
- Configure maximum velocity, acceleration, yaw rate, and obstacle clearance.
- On stale commands or loss of UI connection, command a controlled hover when feasible. Simulation emergency stop pauses physics immediately.
- Expose camera horizontal FOV in degrees and image resolution. Derive vertical FOV from aspect ratio and update intrinsics whenever these settings change.
- Use the active inspection camera intrinsics for visibility and coverage calculations. Third-person viewing must not change inspection coverage.

## Two-panel simulation UI

### Left panel — live drone view

- Large live viewport with first-person / third-person toggle.
- First person: onboard inspection camera, including configurable FOV.
- Third person: chase camera with orbit, zoom, and a visible drone model; use a synchronized visual model if direct URDF rendering is unavailable.
- Overlay operating mode, speed, altitude, collision status, current target, and simulation state.
- Allow selecting a point or region from a frozen first-person frame. Resolve the selection against that frame's depth and pose; reject pixels without a valid surface hit.

### Right panel — controls and inspection state

Arrange vertically with independently resizable sections:

1. **Task and mode:** task description, autonomous / user-directed selection, start, pause, resume, cancel, and reset.
2. **Agent prompt and chat:** editable agent instructions, task messages, concise action explanations, current action, and user overrides. Show pending requests without blocking flight control.
3. **Drone settings:** `vel_cmd`, command frame, speed limits, FOV, resolution, and supplied starting pose.
4. **Interactive 3D GLB map:** orbit/pan/zoom, meter scale, axes, drone pose, camera frustum, target, route, and coverage overlay. Keep its independent camera separate from the onboard camera.
5. **Coverage summary:** percentage, progress bar, inspected / pending / unreachable area, elapsed simulation time, and completion reason.

Use a desktop-local web UI served by the simulation application, with streamed camera images and structured state updates. The UI map and simulator must share the same asset transform and scale. Choose the browser rendering and streaming libraries after checking the local runtime; no hosting is required for the first version.

## Coverage model and map colors

Maintain two distinct quantities: flight free space for planning, and target-surface inspection coverage. The headline progress bar reports target-surface coverage; flying through a room alone does not count as inspecting it.

Partition eligible mesh surfaces into area-weighted samples or patches. A patch is covered only when it passes the configured camera-frustum, occlusion, distance, viewing-angle, and projected-resolution criteria. Use ray tests or depth comparison to verify visibility. Do not count the chase camera.

Suggested colors, accompanied by a legend and text labels:

- Gray: excluded from the task.
- Orange: pending inspection.
- Green: inspected and criteria satisfied.
- Purple: currently unreachable or unobservable.
- Blue outline: active target or planned viewpoint.

Progress = inspected eligible surface area / total eligible surface area. Keep unreachable surfaces in the denominator and report them separately; never silently turn incomplete coverage into 100%. Freeze the task's target definition when a run begins, or explicitly restart the coverage baseline when it changes.

## Inspection modes and completion

### Autonomous mode

1. Convert the task into explicit target regions and inspection criteria. For the first version, provide deterministic presets such as all eligible room surfaces or a selected region; agent proposals must resolve to this structured task model.
2. Build a 3D occupancy representation from scene geometry, inflated for drone size and safety clearance.
3. Generate feasible viewpoints around pending target patches.
4. Rank viewpoints by expected additional coverage and travel cost.
5. Plan collision-free 3D paths; execute through the flight controller and replan as needed.
6. Capture observations and update measured coverage.
7. Finish at the configured coverage threshold, or return a partial result with remaining regions and reasons when no feasible viewpoints remain. Add time and step budgets to prevent endless loops.

### User-directed mode

1. Accept a point or region selected in the starting first-person frame, plus an optional textual task.
2. Resolve it to a 3D surface and define the local inspection region. Show the region before flight so a single pixel does not implicitly mean an entire object.
3. Generate viewpoints and inspect that region using the same planning and coverage components.
4. Finish when its inspection criteria are satisfied, or report why the target could not be completed.

Manual velocity override temporarily takes control from the planner. Resuming autonomy triggers replanning from the current pose. Label this as a manual override within either inspection mode to distinguish it from user-directed target selection.

## Implementation milestones

### 1. Scene and coordinate validation

Load a placeholder GLB, display a live Habitat camera, inspect scene bounds, and verify units with a meter reference. Validate a supplied starting pose is in free space. Record coordinate transforms and hardware-dependent rendering performance.

**Acceptance:** correctly scaled map and camera view agree on a known landmark and position.

### 2. Physical drone and control bridge

Select and pin the existing URDF/controller pair. Import collision geometry, implement motor-driven physics, and synchronize the drone pose and camera with Habitat. Add velocity control, hover, limits, reset, and pause.

**Acceptance:** stable hover, controlled forward/vertical/yaw motion, consistent coordinate conversion, and collision response in a simple scene. These checks validate the integration, not real-world flight fidelity.

### 3. Interactive UI

Build the two-panel layout, first/third-person switch, 3D map, control fields, FOV settings, and state streaming.

**Acceptance:** view switching leaves drone state unchanged; FOV affects the onboard image and frustum consistently; the UI remains responsive during simulation and agent requests.

### 4. Coverage and user-directed inspection

Implement target selection, surface sampling, visibility evaluation, map colors, progress, candidate viewpoints, and 3D route planning.

**Acceptance:** an occluded patch stays pending, revisiting a patch does not double count area, and a selected reachable region can complete an inspection.

### 5. Autonomous inspection and agent interface

Add coverage-driven viewpoint selection, task presets, agent tool calls, bounded execution, cancellation, and manual override.

**Acceptance:** a small scene completes to its configured threshold or produces a clear partial-coverage result. Chat requests cannot bypass collision checks or command limits.

### 6. Reproducible demonstration

Save run configuration, scene and asset identifiers, trajectory, command history, coverage state, completion reason, and representative images. Provide one small-scene scenario for each operating mode.

## Proposed configuration placeholders

```yaml
scene:
  glb: assets/scenes/<scene_name>.glb
  scale_to_meters: 1.0  # Verify against the asset, do not assume.
drone:
  urdf: assets/drone/<quadrotor>.urdf
  controller: <compatible_existing_controller>
  initial_position_m: [0.0, 1.5, 0.0]  # Placeholder; transform into chosen world frame.
  initial_orientation: <supplied_orientation>
control:
  command_frame: body_horizontal_world_vertical
  vel_cmd: [0.0, 0.0, 0.0, 0.0]
  max_speed_mps: 1.0
camera:
  horizontal_fov_deg: 90
  resolution: [640, 480]
inspection:
  mode: autonomous
  task: <inspection_description>
  coverage_threshold: 0.98
  distance_range_m: <task_specific>
  max_view_angle_deg: <task_specific>
  minimum_projected_resolution: <task_specific>
```

All numeric defaults are initial prototype settings to tune. Coverage criteria must be filled in before a run can claim completion.

## Main dependencies and decisions to resolve during implementation

- Availability and compatibility of the selected URDF, flight controller, and physics backend.
- GLB scale, coordinate conventions, collision-mesh quality, and scene rendering performance.
- Whether the task targets structural surfaces, selected regions, or annotated objects; semantic labels require corresponding dataset assets or additional annotation.
- Inspection quality thresholds and acceptable coverage threshold.
- Agent model/provider and credentials, if language-based planning is enabled. Deterministic controls and task presets should work independently.
- Later 3DGS integration: align splats with the original geometry and replace the camera renderer while retaining validated planning and collision geometry.
