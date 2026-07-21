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

    # Fast Web-Optimized Resolution (640x360 for GitHub Pages streaming & fast upload)
    target_w = 640
    target_h = 360

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

    print(f"\n[PROCESSING] Processing DJI Video: '{input_video_path}' ({total_frames} frames, {target_w}x{target_h} @ {fps}fps)...")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.resize(frame, (target_w, target_h))
        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frame_idx += 1

        if p0 is None or len(p0) < min_features:
            p0 = cv2.goodFeaturesToTrack(frame_gray, mask=None, **feature_params)
            mask = np.zeros_like(frame)
            refreshes += 1

        if p0 is not None and len(p0) > 0:
            p1, st, err = cv2.calcOpticalFlowPyrLK(old_gray, frame_gray, p0, None, **lk_params)

            if p1 is not None and st is not None and len(st) == len(p0):
                st_flat = st.ravel() == 1
                good_new = p1[st_flat].reshape(-1, 2)
                good_old = p0[st_flat].reshape(-1, 2)

                mask = (mask * 0.70).astype(np.uint8)

                for new, old in zip(good_new, good_old):
                    a, b = int(new[0]), int(new[1])
                    c, d = int(old[0]), int(old[1])
                    end_x = int(c + (a - c) * 2.5)
                    end_y = int(d + (b - d) * 2.5)

                    cv2.arrowedLine(mask, (c, d), (end_x, end_y), (0, 255, 198), 2, tipLength=0.3)
                    cv2.circle(frame, (c, d), 3, (0, 255, 255), -1)

                output_frame = cv2.add(frame, mask)
                p0 = good_new.reshape(-1, 1, 2)
            else:
                output_frame = frame
                p0 = None
        else:
            output_frame = frame
            p0 = None

        out.write(output_frame)
        old_gray = frame_gray.copy()

    cap.release()
    out.release()

    elapsed = time.time() - start_time
    size_mb = os.path.getsize(output_video_path) / (1024 * 1024)
    print(f"[SUCCESS] Finished '{os.path.basename(output_video_path)}' in {elapsed:.1f}s | Size: {size_mb:.2f} MB")
    return output_video_path

if __name__ == "__main__":
    dji_files = sorted(list(set(glob.glob("DJI_*.MP4") + glob.glob("DJI_*.mp4"))))
    dji_files = [f for f in dji_files if "optical_flow" not in f and not os.path.isdir(f)]

    print(f"Found {len(dji_files)} DJI drone video files to convert for web streaming:")
    for f in dji_files:
        print(f" - {f}")

    results = []
    for video_file in dji_files:
        out_path = process_dji_video(video_file)
        if out_path:
            results.append(out_path)

    print(f"\n🎉 All {len(results)} DJI Drone videos converted to optical flow clips in '{OUTPUT_DIR}'!")
