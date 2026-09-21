from scapy.all import rdpcap, wrpcap, IP
from tqdm import tqdm
import os
from config import *
from data_align import is_pkt_in_windows

# -----------------------
# 【关键】scapy 自动识别 pcap / pcapng，无需任何格式转换
# -----------------------
def load_raw_pcap(pcap_path):
    """加载原始 PCAP 或 PCAPNG 文件，自动识别格式"""
    print(f"📥 加载流量文件：{pcap_path}")
    pkts = rdpcap(pcap_path)  # 自动支持 pcap / pcapng

    filtered_pkts = []
    for pkt in tqdm(pkts, desc="过滤目标设备流量"):
        if IP in pkt:
            src_ip = pkt[IP].src
            dst_ip = pkt[IP].dst
            if src_ip == TARGET_DEVICE_IP or dst_ip == TARGET_DEVICE_IP:
                filtered_pkts.append(pkt)

    print(f"✅ 过滤后保留 {len(filtered_pkts)} 个目标设备IP包")
    return filtered_pkts


def split_pos_unlabeled_pkts(all_pkts, align_windows):
    pos_pkts = []
    unlabeled_pkts = []
    for pkt in tqdm(all_pkts, desc="拆分正包/未标注包"):
        pkt_ts = pkt.time
        if is_pkt_in_windows(pkt_ts, align_windows):
            pos_pkts.append(pkt)
        else:
            unlabeled_pkts.append(pkt)

    print(f"✅ 拆分完成：正包 {len(pos_pkts)} 个 | 未标注包 {len(unlabeled_pkts)} 个")
    return pos_pkts, unlabeled_pkts


def save_aligned_pcap(pkts, save_name, save_dir):
    """
    保存对齐后的流量
    自动保存为 pcapng（你也可以改成 pcap，都支持）
    """
    save_path = os.path.join(save_dir, save_name)

    # 自动保存为 pcapng（Windows 完全支持）
    if not save_path.endswith(".pcapng"):
        save_path = save_path.replace(".pcap", ".pcapng")

    if len(pkts) > 0:
        wrpcap(save_path, pkts)  # scapy 自动写 pcapng
        print(f"💾 对齐流量已保存：{save_path}")
        return save_path
    else:
        print(f"⚠️  无流量可保存：{save_name}")
        return None

import random
import numpy as np

# 流量包划分函数，【随机划分】8:1:1，不再按时间顺序
def split_train_val_test_bags(bags, labels, seed=None):
    # 固定随机种子，保证可复现（你也可以每次不同）
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    # 组合样本和标签，一起打乱
    combined = list(zip(bags, labels))
    random.shuffle(combined)
    bags_shuffled, labels_shuffled = zip(*combined)

    total = len(bags_shuffled)
    train_end = int(total * TRAIN_SPLIT)
    val_end = train_end + int(total * VAL_SPLIT)

    train_bags = list(bags_shuffled[:train_end])
    train_labels = list(labels_shuffled[:train_end])

    val_bags = list(bags_shuffled[train_end:val_end])
    val_labels = list(labels_shuffled[train_end:val_end])

    test_bags = list(bags_shuffled[val_end:])
    test_labels = list(labels_shuffled[val_end:])

    print(f"📊 数据集拆分（【随机】8:1:1）：")
    print(f"训练集：{len(train_bags)} | 验证集：{len(val_bags)} | 测试集：{len(test_bags)}")
    return train_bags, val_bags, test_bags, train_labels, val_labels, test_labels