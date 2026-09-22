import json
import numpy as np
from datetime import datetime

def parse_device_log(json_path):
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)

        event_timestamps = []
        record_list = log_data.get("日志记录", [])
        for item in record_list:
            time_str = item.get("时间", "")
            if time_str:
                try:
                    dt = datetime.fromisoformat(time_str)
                    ts = dt.timestamp()
                    event_timestamps.append(ts)
                except Exception:
                    pass

        event_timestamps = sorted(list(set(event_timestamps)))
        print(f"✅ 解析日志成功！提取到 {len(event_timestamps)} 个设备事件时间戳")
        return event_timestamps

    except Exception as e:
        print(f"❌ 日志解析异常: {e}")
        return []

def calculate_align_windows(event_timestamps, log_delay=2, window_half=5):
    align_windows = []
    for ts in event_timestamps:
        t_center = ts - log_delay
        t_start = t_center - window_half
        t_end = t_center + window_half
        align_windows.append((t_start, t_end))

    print(f"✅ 计算完成！生成 {len(align_windows)} 个10秒正包窗口")
    return align_windows

def split_pkts_by_alignment(pkts, align_windows):
    pos_pkts = []
    unlabeled_pkts = []
    for pkt in pkts:
        if is_pkt_in_windows(pkt.time, align_windows):
            pos_pkts.append(pkt)
        else:
            unlabeled_pkts.append(pkt)
    return pos_pkts, unlabeled_pkts

def is_pkt_in_windows(pkt_timestamp, align_windows):
    for (t_start, t_end) in align_windows:
        if t_start <= pkt_timestamp <= t_end:
            return True
    return False