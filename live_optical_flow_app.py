import cv2
import numpy as np
import os
import glob
import time
import threading
import atexit
import io
import csv
import re
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, Response, jsonify, request, send_file

app = Flask(__name__, template_folder="templates")

OUTPUT_DIR = "optical_flow_results"
UPLOAD_DIR = "uploads"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

class AdvancedOpticalFlowEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.cap = None
        
        # Sources & Reload State
        self.sources = []
        self.current_source = "Live Webcam (0)"
        self.pending_source = "Live Webcam (0)"
        self.need_reload = False
        self.is_video_file = False
        
        # Settings
        self.flow_mode = "sparse"
        self.is_tracking_enabled = False
        self.camera_power_on = False
        
        # Telemetry History Log for Exact CSV Export
        self.telemetry_history = []
        self.frame_counter = 0
        
        # Live Recording State
        self.is_recording = False
        self.recorder = None
        self.record_filename = ""
        
        # Telemetry State
        self.fps = 0.0
        self.tracked_vectors = 0
        self.dx = 0.0
        self.dy = 0.0
        self.speed = 0.0
        self.heading_deg = 0.0
        self.divergence = 0.0
        self.cum_x = 0.0
        self.cum_y = 0.0
        self.obstacle_risk = "LOW"
        
        # Processing parameters
        self.grid_rows = 8
        self.grid_cols = 10
        self.target_w = 640
        self.target_h = 360
        
        self.lk_params = dict(
            winSize=(15, 15),
            maxLevel=2,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03)
        )
        
        self.p0 = None
        self.old_gray = None
        self.mask_trail = None
        
        self.scan_sources()
        atexit.register(self.close_all_resources)

    def close_all_resources(self):
        with self.lock:
            if self.cap is not None:
                print("Releasing camera hardware device...")
                self.cap.release()
                self.cap = None
            if self.recorder is not None:
                self.recorder.release()
                self.recorder = None

    def scan_sources(self):
        """Scans for local video files (DJI drone footage & uploaded videos)."""
        dji_files = sorted(list(set(glob.glob("DJI_*.MP4") + glob.glob("DJI_*.mp4"))))
        dji_files = [f for f in dji_files if "optical_flow" not in f and not os.path.isdir(f)]
        
        valid_exts = ("*.mp4", "*.MP4", "*.webm", "*.mkv", "*.mov", "*.avi", "*.m4v")
        upload_files = []
        for ext in valid_exts:
            upload_files.extend(glob.glob(os.path.join(UPLOAD_DIR, ext)))
        upload_files = sorted(list(set([f for f in upload_files if not os.path.isdir(f)])))
        
        self.sources = ["Live Webcam (0)"] + dji_files + upload_files

    def request_source_load(self, source_name):
        with self.lock:
            self.pending_source = source_name
            self.need_reload = True

    def delete_uploaded_source(self, source_name):
        """Safely deletes an uploaded video file from uploads/ directory."""
        with self.lock:
            if not source_name or ("uploads" not in source_name.lower() and "upload" not in source_name.lower()):
                return False, "Cannot delete built-in drone videos or live webcam."
            
            normalized_path = os.path.normpath(source_name)
            if not os.path.exists(normalized_path):
                alt_path = os.path.normpath(os.path.join(UPLOAD_DIR, os.path.basename(source_name)))
                if os.path.exists(alt_path):
                    normalized_path = alt_path
                else:
                    return False, f"File '{source_name}' does not exist on server."

            if self.current_source == source_name and self.cap is not None:
                self.cap.release()
                self.cap = None

            try:
                os.remove(normalized_path)
                print(f"[DELETE SUCCESS] Deleted uploaded video file: '{normalized_path}'")
                
                self.scan_sources()
                self.pending_source = "Live Webcam (0)"
                self.need_reload = True
                return True, "Video file deleted successfully."
            except Exception as e:
                return False, f"Failed to delete file: {str(e)}"

    def _perform_source_reload_fast(self, target=None):
        if target is None:
            with self.lock:
                target = self.pending_source
                self.need_reload = False
            
        print(f"Loading video source: '{target}'...")

        if self.cap is not None:
            self.cap.release()
            self.cap = None
            time.sleep(0.05)

        if target in ["Live Webcam (0)", "0"]:
            self.is_video_file = False
            if self.camera_power_on:
                self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            else:
                self.cap = None
        else:
            self.cap = cv2.VideoCapture(target)
            self.is_video_file = True
            if not self.cap.isOpened():
                print(f"Warning: Could not open video file '{target}' with default backend.")

        with self.lock:
            self.current_source = target
            self.p0 = None
            self.old_gray = None
            self.mask_trail = None
            self.cum_x, self.cum_y = 0.0, 0.0

    def initialize_spatial_grid(self, gray_frame):
        h, w = gray_frame.shape
        cell_w = w // self.grid_cols
        cell_h = h // self.grid_rows
        
        feature_params = dict(maxCorners=300, qualityLevel=0.1, minDistance=3, blockSize=5)
        
        # Single call to goodFeaturesToTrack on full gray_frame
        call_count = 1
        print(f"[GRID INIT] Made {call_count} call to cv2.goodFeaturesToTrack (down from {self.grid_rows * self.grid_cols})")
        
        corners = cv2.goodFeaturesToTrack(gray_frame, mask=None, **feature_params)
        
        cell_best_corner = {}
        if corners is not None:
            for pt in corners:
                x, y = float(pt[0][0]), float(pt[0][1])
                c = int(x // cell_w)
                r = int(y // cell_h)
                
                c = min(max(c, 0), self.grid_cols - 1)
                r = min(max(r, 0), self.grid_rows - 1)
                
                cell_key = (r, c)
                if cell_key not in cell_best_corner:
                    cell_best_corner[cell_key] = [x, y]
                    
        points = []
        for r in range(self.grid_rows):
            for c in range(self.grid_cols):
                cell_key = (r, c)
                if cell_key in cell_best_corner:
                    points.append([cell_best_corner[cell_key]])
                else:
                    x1 = c * cell_w
                    y1 = r * cell_h
                    points.append([[x1 + cell_w / 2.0, y1 + cell_h / 2.0]])
                    
        return np.array(points, dtype=np.float32) if len(points) > 0 else None

    def start_tracking(self):
        with self.lock:
            self.is_tracking_enabled = True
            if not self.is_video_file:
                self.camera_power_on = True
                self.need_reload = True

    def stop_tracking(self):
        with self.lock:
            self.is_tracking_enabled = False
            if not self.is_video_file:
                self.camera_power_on = False
                if self.cap is not None:
                    print("Turning OFF webcam hardware device...")
                    self.cap.release()
                    self.cap = None

    def reset_grid(self):
        with self.lock:
            self.p0 = None
            self.mask_trail = None
            self.cum_x, self.cum_y = 0.0, 0.0
            self.telemetry_history.clear()
            self.frame_counter = 0
            
            if self.is_video_file and self.cap is not None:
                print(f"Seeking video '{self.current_source}' back to frame 0...")
                self.cap.release()
                self.cap = cv2.VideoCapture(self.current_source)

    def set_flow_mode(self, mode):
        with self.lock:
            self.flow_mode = mode
            self.p0 = None
            self.mask_trail = None

    def start_recording(self):
        with self.lock:
            if not self.is_recording:
                timestamp = int(time.time())
                self.record_filename = os.path.join(OUTPUT_DIR, f"rec_{timestamp}.mp4")
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                self.recorder = cv2.VideoWriter(self.record_filename, fourcc, 30, (self.target_w, self.target_h))
                self.is_recording = True

    def stop_recording(self):
        with self.lock:
            if self.is_recording:
                self.is_recording = False
                if self.recorder is not None:
                    self.recorder.release()
                    self.recorder = None
                return self.record_filename
        return ""

    def get_telemetry(self):
        with self.lock:
            return {
                "fps": round(self.fps, 1),
                "tracked_vectors": self.tracked_vectors,
                "dx": round(self.dx, 2),
                "dy": round(self.dy, 2),
                "speed": round(self.speed, 2),
                "heading_deg": round(self.heading_deg, 1),
                "divergence": round(self.divergence, 3),
                "obstacle_risk": self.obstacle_risk,
                "cum_x": round(self.cum_x, 1),
                "cum_y": round(self.cum_y, 1),
                "flow_mode": self.flow_mode,
                "is_tracking": self.is_tracking_enabled,
                "camera_power_on": self.camera_power_on,
                "is_recording": self.is_recording,
                "current_source": self.current_source,
                "logged_frames": len(self.telemetry_history)
            }

    def export_csv_file(self):
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            "Timestamp", "Frame_Index", "Source", "Flow_Mode", "FPS",
            "Tracked_Vectors", "Displacement_dX_px", "Displacement_dY_px",
            "Speed_px_f", "Heading_deg", "Divergence", "Obstacle_Risk",
            "Cumulative_Drift_X_px", "Cumulative_Drift_Y_px"
        ])
        
        with self.lock:
            for row in self.telemetry_history:
                writer.writerow([
                    row["timestamp"],
                    row["frame_index"],
                    row["source"],
                    row["flow_mode"],
                    row["fps"],
                    row["tracked_vectors"],
                    row["dx"],
                    row["dy"],
                    row["speed"],
                    row["heading_deg"],
                    row["divergence"],
                    row["obstacle_risk"],
                    row["cum_x"],
                    row["cum_y"]
                ])
                
        output.seek(0)
        return output.getvalue()

    def generate_frames(self):
        frame_time = time.time()
        
        while True:
            if self.need_reload:
                self._perform_source_reload_fast()

            with self.lock:
                cap_ref = self.cap
                is_file = self.is_video_file
                tracking_on = self.is_tracking_enabled
                current_src = self.current_source

            frame = None
            if cap_ref is not None and cap_ref.isOpened() and (tracking_on or is_file):
                ret, frame = cap_ref.read()
                
                if not ret and is_file:
                    cap_ref.release()
                    cap_ref = cv2.VideoCapture(current_src)
                    with self.lock:
                        self.cap = cap_ref
                        self.p0 = None
                    ret, frame = cap_ref.read()

            if frame is None or frame.size == 0:
                frame = np.zeros((self.target_h, self.target_w, 3), dtype=np.uint8)
                for x in range(0, self.target_w, 40):
                    cv2.line(frame, (x, 0), (x, self.target_h), (25, 35, 50), 1)
                for y in range(0, self.target_h, 40):
                    cv2.line(frame, (0, y), (self.target_w, y), (25, 35, 50), 1)
                
                cx, cy = self.target_w // 2, self.target_h // 2
                msg = "CAMERA FEED OFF / STOPPED"
                color = (0, 165, 255)
                
                cv2.circle(frame, (cx, cy), 30, color, 2)
                cv2.line(frame, (cx - 20, cy - 20), (cx + 20, cy + 20), color, 2)
                cv2.putText(frame, msg, (150, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
                cv2.putText(frame, "CLICK 'START' TO RUN OPTICAL FLOW TELEMETRY", (100, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 255, 255), 1)
                time.sleep(0.05)

            frame = cv2.resize(frame, (self.target_w, self.target_h))
            frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            if self.mask_trail is None:
                self.mask_trail = np.zeros_like(frame)

            now = time.time()
            dt = now - frame_time
            frame_time = now
            current_fps = 1.0 / dt if dt > 0 else 30.0
            
            with self.lock:
                self.fps = 0.8 * self.fps + 0.2 * current_fps

            if tracking_on and self.old_gray is not None:
                self.frame_counter += 1
                if self.flow_mode == "sparse":
                    if self.p0 is None or len(self.p0) < 15:
                        self.p0 = self.initialize_spatial_grid(frame_gray)
                        self.mask_trail = np.zeros_like(frame)

                    if self.p0 is not None and len(self.p0) > 0 and self.old_gray.shape == frame_gray.shape:
                        p1, st, err = cv2.calcOpticalFlowPyrLK(self.old_gray, frame_gray, self.p0, None, **self.lk_params)
                        
                        if p1 is not None and st is not None and len(st) == len(self.p0):
                            st_flat = (st.ravel() == 1)
                            good_new = p1[st_flat].reshape(-1, 2)
                            good_old = self.p0[st_flat].reshape(-1, 2)

                            if len(good_new) > 0:
                                disp = good_new - good_old
                                dx_vecs = disp[:, 0]
                                dy_vecs = disp[:, 1]
                                
                                med_dx, med_dy = np.median(dx_vecs), np.median(dy_vecs)
                                std_dx, std_dy = np.std(dx_vecs) + 1e-5, np.std(dy_vecs) + 1e-5
                                inliers = (np.abs(dx_vecs - med_dx) < 2.5 * std_dx) & (np.abs(dy_vecs - med_dy) < 2.5 * std_dy)
                                
                                valid_new = good_new[inliers] if np.sum(inliers) > 0 else good_new
                                valid_old = good_old[inliers] if np.sum(inliers) > 0 else good_old
                                avg_dx = float(np.mean(dx_vecs[inliers])) if np.sum(inliers) > 0 else float(med_dx)
                                avg_dy = float(np.mean(dy_vecs[inliers])) if np.sum(inliers) > 0 else float(med_dy)
                                
                                speed_mag = float(np.sqrt(avg_dx**2 + avg_dy**2))
                                heading = float(np.degrees(np.arctan2(avg_dy, avg_dx)))
                                
                                center = np.array([self.target_w / 2.0, self.target_h / 2.0])
                                rad_vecs = valid_new - center
                                rad_dists = np.linalg.norm(rad_vecs, axis=1) + 1e-5
                                rad_disps = np.sum((valid_new - valid_old) * rad_vecs, axis=1) / rad_dists
                                div_val = float(np.mean(rad_disps))
                                risk = "HIGH" if div_val > 2.5 else ("MEDIUM" if div_val > 1.2 else "LOW")

                                with self.lock:
                                    self.tracked_vectors = int(len(valid_new))
                                    self.dx = avg_dx
                                    self.dy = avg_dy
                                    self.speed = speed_mag
                                    self.heading_deg = heading
                                    self.divergence = div_val
                                    self.obstacle_risk = risk
                                    self.cum_x += avg_dx
                                    self.cum_y += avg_dy
                                    
                                    self.telemetry_history.append({
                                        "timestamp": datetime.now().isoformat(),
                                        "frame_index": self.frame_counter,
                                        "source": current_src,
                                        "flow_mode": self.flow_mode,
                                        "fps": round(self.fps, 1),
                                        "tracked_vectors": int(len(valid_new)),
                                        "dx": round(avg_dx, 2),
                                        "dy": round(avg_dy, 2),
                                        "speed": round(speed_mag, 2),
                                        "heading_deg": round(heading, 1),
                                        "divergence": round(div_val, 3),
                                        "obstacle_risk": risk,
                                        "cum_x": round(self.cum_x, 1),
                                        "cum_y": round(self.cum_y, 1)
                                    })

                                self.mask_trail = (self.mask_trail * 0.70).astype(np.uint8)

                                for (new, old) in zip(valid_new, valid_old):
                                    c, d = int(old[0]), int(old[1])
                                    end_x = int(c + (new[0] - old[0]) * 2.5)
                                    end_y = int(d + (new[1] - old[1]) * 2.5)
                                    vec_mag = np.sqrt((new[0] - old[0])**2 + (new[1] - old[1])**2)
                                    
                                    color = (255, 255, 0) if vec_mag < 2.0 else ((0, 255, 198) if vec_mag < 6.0 else (255, 0, 255))
                                    cv2.arrowedLine(self.mask_trail, (c, d), (end_x, end_y), color, 2, tipLength=0.3)
                                    cv2.circle(frame, (c, d), 3, (0, 255, 255), -1)

                                frame = cv2.add(frame, self.mask_trail)
                                self.p0 = valid_new.reshape(-1, 1, 2)
                            else: self.p0 = None
                        else: self.p0 = None
                    else: self.p0 = self.initialize_spatial_grid(frame_gray)

                elif self.flow_mode == "dense":
                    if self.old_gray.shape == frame_gray.shape:
                        flow = cv2.calcOpticalFlowFarneback(self.old_gray, frame_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                        fx, fy = flow[..., 0], flow[..., 1]
                        
                        mag, ang = cv2.cartToPolar(fx, fy)
                        avg_dx, avg_dy = float(np.mean(fx)), float(np.mean(fy))
                        speed_mag = float(np.sqrt(avg_dx**2 + avg_dy**2))
                        heading = float(np.degrees(np.arctan2(avg_dy, avg_dx)))

                        hsv = np.zeros_like(frame)
                        hsv[..., 0] = ang * 180 / np.pi / 2
                        hsv[..., 1] = 255
                        hsv[..., 2] = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX)
                        flow_bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
                        
                        frame = cv2.addWeighted(frame, 0.55, flow_bgr, 0.45, 0)

                        with self.lock:
                            self.tracked_vectors = self.target_w * self.target_h
                            self.dx = avg_dx
                            self.dy = avg_dy
                            self.speed = speed_mag
                            self.heading_deg = heading
                            self.cum_x += avg_dx
                            self.cum_y += avg_dy

                            self.telemetry_history.append({
                                "timestamp": datetime.now().isoformat(),
                                "frame_index": self.frame_counter,
                                "source": current_src,
                                "flow_mode": self.flow_mode,
                                "fps": round(self.fps, 1),
                                "tracked_vectors": self.tracked_vectors,
                                "dx": round(avg_dx, 2),
                                "dy": round(avg_dy, 2),
                                "speed": round(speed_mag, 2),
                                "heading_deg": round(heading, 1),
                                "divergence": round(self.divergence, 3),
                                "obstacle_risk": self.obstacle_risk,
                                "cum_x": round(self.cum_x, 1),
                                "cum_y": round(self.cum_y, 1)
                            })

            self.old_gray = frame_gray.copy()

            with self.lock:
                if self.is_recording and self.recorder is not None:
                    self.recorder.write(frame)

            ret_encode, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            frame_bytes = buffer.tobytes()

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

engine = AdvancedOpticalFlowEngine()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(engine.generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/sources', methods=['GET'])
def api_sources():
    engine.scan_sources()
    return jsonify({"sources": engine.sources, "current": engine.current_source})

@app.route('/api/select_source', methods=['POST'])
def api_select_source():
    data = request.get_json() or {}
    source = data.get("source", "0")
    engine.request_source_load(source)
    return jsonify({"status": "reload_requested", "source": source})

@app.route('/api/upload_video', methods=['POST'])
def api_upload_video():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400

    raw_name = os.path.basename(file.filename)
    clean_name = re.sub(r'[^a-zA-Z0-9_\.-]', '_', raw_name)
    if not clean_name:
        clean_name = f"uploaded_video_{int(time.time())}.mp4"

    save_path = os.path.normpath(os.path.join(UPLOAD_DIR, clean_name))
    file.save(save_path)
    
    print(f"[UPLOAD SUCCESS] Video Upload Received: '{save_path}'")
    engine.scan_sources()
    engine.request_source_load(save_path)
    return jsonify({"status": "uploaded", "source": save_path, "sources": engine.sources})

@app.route('/api/delete_source', methods=['POST'])
def api_delete_source():
    data = request.get_json() or {}
    source = data.get("source", "")
    success, msg = engine.delete_uploaded_source(source)
    if success:
        return jsonify({"status": "deleted", "message": msg, "sources": engine.sources, "current": engine.current_source})
    else:
        return jsonify({"error": msg}), 400

@app.route('/api/export_csv', methods=['GET'])
def api_export_csv():
    csv_data = engine.export_csv_file()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=optical_flow_telemetry_{timestamp}.csv"}
    )

@app.route('/api/set_mode', methods=['POST'])
def api_set_mode():
    data = request.get_json() or {}
    mode = data.get("mode", "sparse")
    engine.set_flow_mode(mode)
    return jsonify({"status": "mode_set", "mode": mode})

@app.route('/api/start', methods=['POST'])
def api_start():
    engine.start_tracking()
    return jsonify({"status": "started"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    engine.stop_tracking()
    return jsonify({"status": "stopped"})

@app.route('/api/reset', methods=['POST'])
def api_reset():
    engine.reset_grid()
    return jsonify({"status": "reset"})

@app.route('/api/record_start', methods=['POST'])
def api_record_start():
    engine.start_recording()
    return jsonify({"status": "recording_started", "filename": engine.record_filename})

@app.route('/api/record_stop', methods=['POST'])
def api_record_stop():
    fn = engine.stop_recording()
    return jsonify({"status": "recording_stopped", "filename": fn})

@app.route('/api/telemetry', methods=['GET'])
def api_telemetry():
    return jsonify(engine.get_telemetry())

if __name__ == '__main__':
    print("Starting Advanced Optical Flow Server at http://127.0.0.1:5050 ...")
    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)
