import cv2
import numpy as np
import os
import glob
import time

OUTPUT_DIR = "optical_flow_results"

def process_dji_video(input_video_path, output_dir=OUTPUT_DIR, min_features=10, max_corners=100):
    if not os.path.exists(input_video_path):
        print(f"Error: {input_video_path} does not exist.")
        return None

    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_video_path))[0]
    output_video_path = os.path.join(output_dir, f"{base_name}_optical_flow.mp4")

    cap = cv2.VideoCapture(input_video_path)
    if not cap.isOpened():
        print(f"Failed to open video: {input_video_path}")
        return None

    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps    = int(cap.get(cv2.CAP_PROP_FPS)) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    target_w = 960
    target_h = int(target_w * orig_h / orig_w)

    feature_params = dict(maxCorners=max_corners, qualityLevel=0.3, minDistance=7, blockSize=7)
    lk_params = dict(winSize=(15, 15), maxLevel=2, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03))

    ret, old_frame = cap.read()
    if not ret:
        print(f"Could not read first frame of {input_video_path}")
        return None

    old_frame = cv2.resize(old_frame, (target_w, target_h))
    old_gray  = cv2.cvtColor(old_frame, cv2.COLOR_BGR2GRAY)
    p0        = cv2.goodFeaturesToTrack(old_gray, mask=None, **feature_params)
    mask      = np.zeros_like(old_frame)

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out    = cv2.VideoWriter(output_video_path, fourcc, fps, (target_w, target_h))

    frame_idx = 0
    refreshes = 0
    start_time = time.time()

    print(f"\n🛸 Processing DJI Video: '{input_video_path}' ({total_frames} frames, {orig_w}x{orig_h} @ {fps}fps)...")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.resize(frame, (target_w, target_h))
        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Dynamic Point Refresh
        if p0 is None or len(p0) < min_features:
            p0 = cv2.goodFeaturesToTrack(frame_gray, mask=None, **feature_params)
            mask = np.zeros_like(frame)
            refreshes += 1

        if p0 is not None and len(p0) > 0:
            p1, st, err = cv2.calcOpticalFlowPyrLK(old_gray, frame_gray, p0, None, **lk_params)
            if p1 is not None and st is not None:
                good_new = p1[st == 1]
                good_old = p0[st == 1]

                for i, (new, old) in enumerate(zip(good_new, good_old)):
                    a, b = new.ravel()
                    c, d = old.ravel()
                    mask = cv2.line(mask, (int(a), int(b)), (int(c), int(d)), (0, 255, 0), 2)
                    frame = cv2.circle(frame, (int(a), int(b)), 4, (0, 255, 255), -1)

                img = cv2.add(frame, mask)
                
                # Telemetry HUD
                cv2.putText(img, f"DJI Drone LK Optical Flow | Frame: {frame_idx}/{total_frames}", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
                cv2.putText(img, f"Tracked Features: {len(good_new)} | Refreshes: {refreshes}", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (0, 255, 0), 2)

                out.write(img)
                old_gray = frame_gray.copy()
                p0 = good_new.reshape(-1, 1, 2)
            else:
                out.write(frame)
                old_gray = frame_gray.copy()
                p0 = None
        else:
            out.write(frame)
            old_gray = frame_gray.copy()

        frame_idx += 1

    cap.release()
    out.release()
    elapsed = time.time() - start_time
    print(f"DONE: '{input_video_path}' in {elapsed:.2f}s -> Saved: '{output_video_path}'")
    return output_video_path

def main():
    dji_files = sorted(list(set(glob.glob("DJI_*.MP4") + glob.glob("DJI_*.mp4"))))
    dji_files = [f for f in dji_files if "optical_flow" not in f]

    print(f"Found {len(dji_files)} DJI drone video file(s):")
    for idx, f in enumerate(dji_files, 1):
        print(f" [{idx}] {f}")

    if not dji_files:
        print("No DJI MP4 video files found in current directory.")
        return

    print(f"\nProcessing all DJI drone videos into '{OUTPUT_DIR}' folder...")
    for f in dji_files:
        process_dji_video(f)

    print(f"\nAll DJI drone video processing complete! Outputs saved in '{OUTPUT_DIR}'.")

if __name__ == "__main__":
    main()
