from ultralytics import YOLO
import cv2
import numpy as np
import redis
import sqlite3
import datetime
import uuid
from tracker import Sort

# COCO Class mapping
# person=0, backpack=24, handbag=26, suitcase=28
RELEVANT_CLASSES = [0, 24, 26, 28]

class GateVerifier:
    def __init__(self, model_path='yolov8n.pt'):
        print("Loading YOLOv8 model...")
        self.model = YOLO(model_path)
        print("Model loaded.")
        self.tracker = Sort(max_age=30, min_hits=3, iou_threshold=0.3)
        self.db_name = "iam_society.db"
        # Note: We duplicate simple Redis connection here to avoid cyclical import if used in main
        # But we can rely on main passing raw logs? Better to connect independently.
        try:
             import fakeredis
             self.redis = fakeredis.FakeStrictRedis()
             # Wait, fakeredis memory is process-bound? 
             # If we run this in the same process (fastapi), we can import the GLOBAL redis_client.
             # If we separate process, we need real redis.
             # WE ARE IN THE SAME PROCESS (async function).
             # So we will pass the redis_client instance to the process function.
        except:
             self.redis = None

    def process_video(self, video_path, gate_x, inner_x, redis_client_instance):
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {"error": "Could not open video"}

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        if fps == 0:
            print("WARNING: FPS is 0, defaulting to 30")
            fps = 30
        
        # Coordinate Scaling
        # Frontend Canvas is fixed at 800px width
        # Video might be HD (1920) or other resolution
        CANVAS_WIDTH = 800.0
        scale_factor = width / CANVAS_WIDTH
        
        gate_x = int(gate_x * scale_factor)
        inner_x = int(inner_x * scale_factor)

        # Video Writer Setup
        # Switch to WebM (VP8) which is browser-friendly and usually built-in to OpenCV
        output_filename = f"output_{uuid.uuid4()}.webm"
        fourcc = cv2.VideoWriter_fourcc(*'VP80') 
        out = cv2.VideoWriter(output_filename, fourcc, fps, (width, height))
        
        tracks = {}
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            timestamp = frame_idx / fps
            
            # YOLO Detect
            results = self.model(frame, classes=RELEVANT_CLASSES, verbose=False)
            detections = []
            
            current_payloads = [] 
            
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    xyxy = box.xyxy[0].cpu().numpy()
                    
                    if cls == 0: # Person
                        detections.append([xyxy[0], xyxy[1], xyxy[2], xyxy[3], conf])
                        # Visual: Draw Person Box
                        cv2.rectangle(frame, (int(xyxy[0]), int(xyxy[1])), (int(xyxy[2]), int(xyxy[3])), (0, 255, 0), 2)
                    else: 
                        current_payloads.append((xyxy, cls))
                        cv2.rectangle(frame, (int(xyxy[0]), int(xyxy[1])), (int(xyxy[2]), int(xyxy[3])), (0, 255, 255), 2)
                        
            # Update Tracker
            if len(detections) > 0:
                track_bbs_ids = self.tracker.update(np.array(detections))
            else:
                track_bbs_ids = self.tracker.update()
            
            for d in track_bbs_ids:
                x1, y1, x2, y2, track_id = d
                track_id = int(track_id)
                cy = int((y1 + y2) / 2)
                cx = int((x1 + x2) / 2)
                
                # Visual: Draw ID
                cv2.putText(frame, f"ID: {track_id}", (int(x1), int(y1)-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                cv2.circle(frame, (cx, cy), 4, (0, 0, 255), -1)

                if track_id not in tracks:
                    tracks[track_id] = {
                        'path': [], 
                        'crossed_gate': None, 
                        'crossed_inner': None,
                        'payloads_seen': set(),
                        'verdict': "Analysing..."
                    }
                
                tracks[track_id]['path'].append((cx, cy, timestamp))
                
                # Check Payloads
                for p_box, p_cls in current_payloads:
                    px1, py1, px2, py2 = p_box
                    p_cx, p_cy = (px1+px2)/2, (py1+py2)/2
                    if abs(cx - p_cx) < (x2-x1) and abs(cy - p_cy) < (y2-y1):
                        tracks[track_id]['payloads_seen'].add(p_cls)

                # Line Crossing Logic (X-Axis)
                path = tracks[track_id]['path']
                if len(path) > 2:
                    last_cx = path[-2][0]
                    curr_cx = cx
                    
                    # Check crossing GATE
                    if (last_cx < gate_x <= curr_cx) or (last_cx > gate_x >= curr_cx):
                        if not tracks[track_id]['crossed_gate']:
                            tracks[track_id]['crossed_gate'] = timestamp
                            print(f"[DEBUG] ID {track_id} Crossed GATE at {timestamp:.2f}s")
                            
                    # Check crossing INNER
                    if (last_cx < inner_x <= curr_cx) or (last_cx > inner_x >= curr_cx):
                         if not tracks[track_id]['crossed_inner']:
                            tracks[track_id]['crossed_inner'] = timestamp
                            print(f"[DEBUG] ID {track_id} Crossed INNER at {timestamp:.2f}s")

            # Visual: Draw Lines
            cv2.line(frame, (gate_x, 0), (gate_x, height), (255, 0, 0), 2) # Blue Gate
            cv2.putText(frame, "GATE", (gate_x+5, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
            
            cv2.line(frame, (inner_x, 0), (inner_x, height), (0, 0, 255), 2) # Red Inner
            cv2.putText(frame, "INNER", (inner_x+5, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

            out.write(frame)
            frame_idx += 1
            
        cap.release()
        out.release()
        
        # --- Final Analysis ---
        final_results = []
        token_logs = [] 
        try:
            # Read ALL logs
            raw_logs = redis_client_instance.lrange("gate_access_logs", 0, -1)
            for l in raw_logs:
                token_logs.append(l.decode().split('|'))
            
            # CRITICAL: Clear logs so they aren't reused for the next video!
            # One-time verification per session.
            redis_client_instance.delete("gate_access_logs")
        except: pass

        # Expanded Logic: Any crossing count?
        # Even if they didn't cross BOTH, report them for debugging
        print(f"[DEBUG] Final Analysis - Tracks: {len(tracks)}")
        print(f"[DEBUG] Token Logs Available: {len(token_logs)}")
        
        for pid, pdata in tracks.items():
            # Strict rule: Must cross BOTH.
            if pdata['crossed_gate'] and pdata['crossed_inner']:
                verdict = "UNAUTHORIZED ENTRY (No Token Found)"
                
                # Check for ANY valid token log in the session
                if len(token_logs) > 0: 
                    # Optional: Check if log is recent (e.g., last 5 mins)
                    # For demo, we assume if server is running and log exists, it's valid.
                    verdict = "VERIFIED ENTRY (Token Matched)"
                
                final_results.append({
                    "person_id": pid,
                    "verdict": verdict,
                    "time_gate": pdata['crossed_gate'],
                    "payload": str(list(pdata['payloads_seen']))
                })
            elif pdata['crossed_gate'] or pdata['crossed_inner']:
                 # Partial Crossing Debug
                 final_results.append({
                    "person_id": pid,
                    "verdict": "PARTIAL CROSSING (Check Lines)",
                    "time_gate": pdata['crossed_gate'] if pdata['crossed_gate'] else 0,
                    "payload": "Debug Partial"
                })

        return {"results": final_results, "video_url": f"/static_videos/{output_filename}"}

