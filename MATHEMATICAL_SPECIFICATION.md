# 📐 Monocular Optical Flow Telemetry Specification

This document provides the universal mathematical equations and inter-relationships for all parameters in your Monocular Optical Flow system. It is formatted to render cleanly across all Markdown previewers (VS Code, GitHub, Obsidian, Jupyter).

---

## 📌 1. Parameter Summary & Formulas

| Parameter Name | Symbol | Standard Formula | Units | Physical / Engineering Meaning |
| :--- | :--- | :--- | :--- | :--- |
| **Frames Per Second** | **FPS** | `FPS = 1 / Δt` | Hz (fps) | Sampling frequency of video processing pipeline. |
| **Tracked Vectors** | **N** | `N = Count(inlier points)` | Count | Statistical sample size & measurement confidence. |
| **Horizontal Displacement** | **ΔX** | `ΔX = Mean(x_k - x_{k-1})` | px/frame | Average pixel shift along image horizontal X-axis. |
| **Vertical Displacement** | **ΔY** | `ΔY = Mean(y_k - y_{k-1})` | px/frame | Average pixel shift along image vertical Y-axis. |
| **Speed Magnitude** | **S** | `S = √( ΔX² + ΔY² )` | px/frame | Scalar 2D magnitude of movement speed. |
| **Heading Angle** | **θ** | `θ = atan2(ΔY, ΔX) × (180 / π)` | degrees (°) | Angular direction of drone motion [-180°, +180°]. |
| **Cumulative Drift X** | **ΣX** | `ΣX_k = ΣX_{k-1} + ΔX_k` | pixels | Integrated horizontal drift from initial takeoff. |
| **Cumulative Drift Y** | **ΣY** | `ΣY_k = ΣY_{k-1} + ΔY_k` | pixels | Integrated vertical drift from initial takeoff. |
| **Flow Divergence** | **Div** | `Div = Mean( (p_i - c) · v_i / ||p_i - c|| )` | sec⁻¹ | Radial expansion rate outward from image center. |
| **Obstacle Risk** | **Risk** | `Risk = HIGH (Div > 2.5), MED (1.2-2.5), LOW (< 1.2)` | Level | Automatic collision alert classification. |

---

## 2. Step-by-Step Detailed Mathematical Formulations

### ⏱️ 2.1 Processing Frame Rate (FPS)
* **Equation**:
  $$\text{FPS} = \frac{1}{\Delta t} = \frac{1}{t_k - t_{k-1}}$$
* **Description**: Measures elapsed processing time between consecutive frames.
* **Conversion**: Converts per-frame measurements ($\text{px/f}$) into per-second rates ($\text{px/s}$).

---

### 🎯 2.2 Tracked Feature Vectors (N)
* **Equation**:
  $$N = \sum_{i=1}^{M} \text{Inlier}(p_i)$$
* **Description**: Total count of high-confidence corner features tracked by Pyramid Lucas-Kanade.

---

### ↔️ 2.3 Horizontal & Vertical Mean Displacements (ΔX, ΔY)
* **Equations**:
  $$\Delta X = \frac{1}{N} \sum_{i=1}^{N} (x_{i, k} - x_{i, k-1})$$

  $$\Delta Y = \frac{1}{N} \sum_{i=1}^{N} (y_{i, k} - y_{i, k-1})$$

* **Inter-relationship**: $\Delta X$ and $\Delta Y$ are the fundamental root outputs of the optical flow engine. All downstream metrics are derived from this pair.

---

### ⚡ 2.4 Speed Magnitude (S) & Ground Speed (V)
* **Pixel Speed Equation**:
  $$S = \sqrt{\Delta X^2 + \Delta Y^2} \quad [\text{px/frame}]$$

* **Real Metric Velocity Equation**:
  $$V = S \times \text{FPS} \times \left( \frac{Z}{f} \right) \quad [\text{m/s}]$$
  *(where $Z$ is altitude height above ground, and $f$ is camera focal length).*

---

### 🧭 2.5 Navigation Heading Angle (θ)
* **Equation**:
  $$\theta = \operatorname{atan2}(\Delta Y, \Delta X) \times \frac{180}{\pi} \quad [^\circ]$$

* **Compass Direction Mapping**:
  * `0.0°` = Motion to the Right ($\Delta X > 0, \Delta Y = 0$)
  * `+90.0°` = Motion Downwards ($\Delta X = 0, \Delta Y > 0$)
  * `±180.0°` = Motion to the Left ($\Delta X < 0, \Delta Y = 0$)
  * `-90.0°` = Motion Upwards ($\Delta X = 0, \Delta Y < 0$)

---

### 📍 2.6 Cumulative Drift (ΣX, ΣY) & Total Drift Distance (D)
* **Integrated Equations**:
  $$\Sigma X_k = \Sigma X_{k-1} + \Delta X_k$$

  $$\Sigma Y_k = \Sigma Y_{k-1} + \Delta Y_k$$

* **Total Distance from Takeoff**:
  $$D = \sqrt{(\Sigma X)^2 + (\Sigma Y)^2} \quad [\text{pixels}]$$

---

### 🌊 2.7 Flow Divergence & Collision Risk
* **Divergence Equation**:
  $$\text{Divergence} = \frac{1}{N} \sum_{i=1}^{N} \frac{(x_i - c_x) u_i + (y_i - c_y) v_i}{\sqrt{(x_i - c_x)^2 + (y_i - c_y)^2}}$$

* **Risk Decision Rule**:
  $$\text{Obstacle Risk} = \begin{cases} \text{HIGH}, & \text{if Divergence } > 2.50 \\ \text{MEDIUM}, & \text{if } 1.20 < \text{Divergence } \le 2.50 \\ \text{LOW}, & \text{if Divergence } \le 1.20 \end{cases}$$

---

## 3. Dataflow Connection Diagram

```
                 +--------------------------------+
                 |    Frame (k-1) & Frame (k)     |
                 +---------------+----------------+
                                 |
                                 v  Lucas-Kanade Tracking
                 +---------------+----------------+
                 | Feature Displacements (dx, dy) |
                 +---------------+----------------+
                                 |
                                 v  Mean Filter
                 +---------------+----------------+
                 |   Displacement Pair (ΔX, ΔY)   |
                 +----+----------+----------+-----+
                      |          |          |
         +------------+          |          +------------+
         v                       v                       v
 +---------------+       +---------------+       +---------------+
 | Speed (S)     |       | Heading (θ)   |       | Cum. Drift    |
 | √(ΔX² + ΔY²)  |       | atan2(ΔY, ΔX) |       | ΣX+ΔX, ΣY+ΔY  |
 +---------------+       +---------------+       +---------------+
```
