import numpy as np
from scapy.all import IP, TCP, UDP
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm
from config import *


def extract_pkt_features(pkt):
    """
    提取单个数据包的7维特征
    返回：特征字典
    """
    feat = {}
    feat["pkt_time"] = pkt.time
    feat["inter_arrival"] = 0.0  # 后续填充
    feat["is_upstream"] = 1 if pkt[IP].src == TARGET_DEVICE_IP else 0
    feat["pkt_len"] = pkt[IP].len
    feat["ttl"] = pkt[IP].ttl

    if TCP in pkt:
        feat["pkt_type"] = 6
        feat["window_size"] = pkt[TCP].window
        feat["payload_len"] = len(pkt[TCP].payload) if pkt[TCP].payload else 0
    elif UDP in pkt:
        feat["pkt_type"] = 17
        feat["window_size"] = 0
        feat["payload_len"] = len(pkt[UDP].payload) if pkt[UDP].payload else 0
    else:
        feat["pkt_type"] = 0
        feat["window_size"] = 0
        feat["payload_len"] = 0
    return feat


def build_bags_from_pkts(pkts, time_window=TIME_WINDOW):
    """
    将数据包按时间窗打包（MIL的Bag）
    返回：打包后的特征列表、标签列表
    """
    if len(pkts) == 0:
        return [], []

    # 提取所有包的特征并排序
    pkt_feats = []
    last_pkt_time = None
    for pkt in tqdm(pkts, desc="提取数据包特征"):
        feat = extract_pkt_features(pkt)
        if last_pkt_time is not None:
            feat["inter_arrival"] = feat["pkt_time"] - last_pkt_time
        pkt_feats.append(feat)
        last_pkt_time = feat["pkt_time"]

    # 按时间排序
    pkt_feats_sorted = sorted(pkt_feats, key=lambda x: x["pkt_time"])

    # 按时间窗打包
    bags_raw = []
    current_bag = []
    start_time = pkt_feats_sorted[0]["pkt_time"]
    for f in pkt_feats_sorted:
        if f["pkt_time"] - start_time > time_window:
            bags_raw.append(current_bag)
            current_bag = [f]
            start_time = f["pkt_time"]
        else:
            current_bag.append(f)
    if current_bag:
        bags_raw.append(current_bag)

    print(f"✅ 按 {time_window}s 时间窗打包完成，共 {len(bags_raw)} 个流量包")
    return bags_raw


def normalize_features(bags_raw, scaler=None):
    """
    特征标准化
    bags_raw: 原始流量包特征列表
    scaler: 已训练的scaler（推理时用），None则新建（训练时用）
    返回：标准化后的流量包、scaler
    """
    feature_cols = ["pkt_len", "is_upstream", "inter_arrival", "pkt_type", "ttl", "window_size", "payload_len"]

    # 拼接所有特征
    all_feats = []
    for bag in bags_raw:
        arr = np.array([[ff[col] for col in feature_cols] for ff in bag], dtype=np.float32)
        all_feats.append(arr)
    all_feats_concat = np.vstack(all_feats).astype(np.float32)

    # 标准化
    if scaler is None:
        scaler = StandardScaler()
        all_feats_scaled = scaler.fit_transform(all_feats_concat).astype(np.float32)
    else:
        all_feats_scaled = scaler.transform(all_feats_concat).astype(np.float32)

    # 重新组装成包
    bags_scaled = []
    idx = 0
    for b in bags_raw:
        L = len(b)
        bags_scaled.append(all_feats_scaled[idx:idx + L].astype(np.float32))
        idx += L

    return bags_scaled, scaler