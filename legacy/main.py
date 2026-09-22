import os
import numpy as np
import torch
from tqdm import tqdm
from scapy.all import rdpcap, wrpcap, IP, TCP, UDP
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

# ===================== 模块导入 =====================
from config import *
from data_align import parse_device_log, calculate_align_windows, split_pkts_by_alignment, is_pkt_in_windows
from pcap_processor import load_raw_pcap, save_aligned_pcap, split_train_val_test_bags
from feature_extractor import build_bags_from_pkts, normalize_features, extract_pkt_features
from model_def import AEMILPU, TrafficDataset

# ===================== 训练函数 =====================
def train_model(train_loader, val_loader):
    model = AEMILPU().to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    best_val_loss = float('inf')

    print("🚀 开始训练模型（50轮）")
    for epoch in range(EPOCHS):
        model.train()
        total_train_loss = 0.0

        for bag, label in train_loader:
            optimizer.zero_grad()
            recon_loss, bag_prob = model(bag[0])
            pu_loss = model.pu_loss(bag_prob, label)
            loss = recon_loss + pu_loss
            loss.backward()
            optimizer.step()
            total_train_loss += loss.item()

        avg_train_loss = total_train_loss / len(train_loader)

        model.eval()
        total_val_loss = 0.0
        with torch.no_grad():
            for bag, label in val_loader:
                recon_loss, bag_prob = model(bag[0])
                pu_loss = model.pu_loss(bag_prob, label)
                loss = recon_loss + pu_loss
                total_val_loss += loss.item()

        avg_val_loss = total_val_loss / len(val_loader)

        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            torch.save(model.state_dict(), BEST_MODEL_NAME)

        tqdm.write(f"训练进度: {epoch+1}/{EPOCHS} | 训练损失={avg_train_loss:.4f} | 验证损失={avg_val_loss:.4f}")

    print(f"\n✅ 训练完成！最优验证损失：{best_val_loss:.4f}")
    return model, best_val_loss


# ===================== 评估函数（可验算版：无报错版） =====================
def evaluate_model(model, loader, threshold=0.4):
    model.eval()
    y_true = []
    y_pred = []
    y_prob = []

    with torch.no_grad():
        for bag, label in loader:
            _, bag_prob = model(bag[0])
            prob = bag_prob.item()
            pred = 1 if prob > threshold else 0

            y_true.append(label.item())
            y_pred.append(pred)
            y_prob.append(prob)

    # 手动计算 TP, TN, FP, FN
    TP = 0  # 真正例：真=1，预测=1
    TN = 0  # 真负例：真=0，预测=0
    FP = 0  # 假正例：真=0，预测=1
    FN = 0  # 假负例：真=1，预测=0

    print("\n" + "=" * 80)
    print(f"📋 详细测试样本（阈值={threshold}）")
    print("=" * 80)
    print(f"{'序号':<4} {'真实标签':<8} {'输出概率':<10} {'预测标签':<8} {'结果':<10}")
    print("-" * 80)

    for idx, (t, p, prob) in enumerate(zip(y_true, y_pred, y_prob)):
        res = ""
        if t == 1 and p == 1:
            TP += 1
            res = "TP(真正例)"
        elif t == 0 and p == 0:
            TN += 1
            res = "TN(真负例)"
        elif t == 0 and p == 1:
            FP += 1
            res = "FP(假正例)"
        elif t == 1 and p == 0:
            FN += 1
            res = "FN(假负例)"

        # 修复格式问题！
        print(f"{idx + 1:<4} {t:<8} {prob:<10.4f} {p:<8} {res:<10}")

    # 计算指标
    accuracy = (TP + TN) / (TP + TN + FP + FN) if (TP + TN + FP + FN) > 0 else 0
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0
    recall = TP / (TP + FN) if (TP + FN) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    # 打印混淆矩阵统计
    print("\n" + "-" * 80)
    print(f"📊 混淆矩阵统计：")
    print(f"真正例(TP)：{TP} 个\t\t真负例(TN)：{TN} 个")
    print(f"假正例(FP)：{FP} 个\t\t假负例(FN)：{FN} 个")
    print("-" * 80)

    # 打印指标
    print(f"\n🎯 评估指标：")
    print(f"准确率  = (TP+TN)/总 = ({TP}+{TN})/{len(y_true)} = {accuracy:.4f}")
    print(f"精确率  = TP/(TP+FP) = {TP}/({TP}+{FP}) = {precision:.4f}")
    print(f"召回率  = TP/(TP+FN) = {TP}/({TP}+{FN}) = {recall:.4f}")
    print(f"F1分数  = 2*P*R/(P+R) = {f1:.4f}")
    print("-" * 80)

    return accuracy, precision, recall, f1

# ===================== 数据加载 =====================
def create_data_loaders(train_bags, val_bags, test_bags, train_labels, val_labels, test_labels):
    train_dataset = TrafficDataset(train_bags, train_labels)
    val_dataset = TrafficDataset(val_bags, val_labels)
    test_dataset = TrafficDataset(test_bags, test_labels)

    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=1, shuffle=False)
    val_loader = torch.utils.data.DataLoader(val_dataset, batch_size=1, shuffle=False)
    test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=1, shuffle=False)
    return train_loader, val_loader, test_loader

# ===================== 主流程（交叉验证版） =====================
def main():
    # 配置：交叉验证轮数（你想跑几轮就改几轮）
    N_FOLDS = 5
    all_test_acc = []
    all_test_prec = []
    all_test_recall = []
    all_test_f1 = []

    print("===== 步骤1：解析设备日志 =====")
    event_timestamps = parse_device_log(JSON_LOG_PATH)
    if not event_timestamps:
        return

    print("\n===== 步骤2：计算10秒对齐窗口 =====")
    align_windows = calculate_align_windows(event_timestamps, LOG_DELAY, TIME_WINDOW_HALF)

    print("\n===== 步骤3：加载原始 PCAP =====")
    target_pkts = load_raw_pcap(PCAP_RAW_PATH)

    print("\n===== 步骤4：对齐划分正包/未标注包（仅执行1次） =====")
    pos_pkts, unlabeled_pkts = split_pkts_by_alignment(target_pkts, align_windows)

    print("\n===== 步骤5：保存对齐文件 =====")
    save_aligned_pcap(pos_pkts, POS_BAG_PCAP_NAME, ALIGNED_PCAP_DIR)
    save_aligned_pcap(unlabeled_pkts, UNLABELED_PCAP_NAME, ALIGNED_PCAP_DIR)

    # ===================== 样本生成（不变） =====================
    print("\n===== 步骤6：严格57个正包 + 空隙未标注包（完美版） =====")
    pkts_sorted = sorted(target_pkts, key=lambda x: x.time)
    final_bags = []
    final_labels = []

    in_any_window = [False] * len(pkts_sorted)
    for i, pkt in enumerate(pkts_sorted):
        for (s, e) in align_windows:
            if s <= pkt.time <= e:
                in_any_window[i] = True
                break

    sorted_windows = sorted(align_windows, key=lambda x: x[0])
    last_window_end = None

    for t_start, t_end in sorted_windows:
        if last_window_end is not None:
            gap_pkts = []
            for i, pkt in enumerate(pkts_sorted):
                if not in_any_window[i] and last_window_end < pkt.time < t_start:
                    gap_pkts.append(pkt)
            if gap_pkts:
                final_bags.append(gap_pkts)
                final_labels.append(0)

        current_pkt_list = []
        for pkt in pkts_sorted:
            if t_start <= pkt.time <= t_end:
                current_pkt_list.append(pkt)
        if current_pkt_list:
            final_bags.append(current_pkt_list)
            final_labels.append(1)

        last_window_end = t_end

    last_gap = []
    for i, pkt in enumerate(pkts_sorted):
        if not in_any_window[i] and pkt.time > last_window_end:
            last_gap.append(pkt)
    if last_gap:
        final_bags.append(last_gap)
        final_labels.append(0)

    # 特征提取
    all_bags_raw = []
    for bag in final_bags:
        bag_feats = []
        last_t = None
        for pkt in bag:
            feat = extract_pkt_features(pkt)
            feat["inter_arrival"] = pkt.time - last_t if last_t else 0.0
            bag_feats.append(feat)
            last_t = pkt.time
        all_bags_raw.append(bag_feats)

    all_bags, scaler = normalize_features(all_bags_raw)
    all_labels = final_labels

    print(f"✅ 最终样本：正包 {sum(final_labels)} 个，未标注包 {len(final_labels) - sum(final_labels)} 个，总计 {len(final_bags)} 个")

    # ===================== 开始交叉验证（多轮随机划分 + 训练） =====================
    print(f"\n🚀 开始 {N_FOLDS} 轮交叉验证（随机8:1:1划分）")
    print("=" * 100)

    for fold in range(N_FOLDS):
        print(f"\n📌 第 {fold + 1}/{N_FOLDS} 轮")
        print("-" * 80)

        # 每一轮使用【不同随机种子】→ 不同划分
        train_bags, val_bags, test_bags, train_labels, val_labels, test_labels = split_train_val_test_bags(
            all_bags, all_labels, seed=fold
        )

        train_loader, val_loader, test_loader = create_data_loaders(
            train_bags, val_bags, test_bags, train_labels, val_labels, test_labels
        )

        # 训练
        best_model, best_val_loss = train_model(train_loader, val_loader)

        # 评估
        print(f"\n📝 第 {fold + 1} 轮评估结果")
        val_acc, val_prec, val_recall, val_f1 = evaluate_model(best_model, val_loader)
        test_acc, test_prec, test_recall, test_f1 = evaluate_model(best_model, test_loader)

        # 保存本轮指标
        all_test_acc.append(test_acc)
        all_test_prec.append(test_prec)
        all_test_recall.append(test_recall)
        all_test_f1.append(test_f1)

    # ===================== 输出交叉验证最终平均指标 =====================
    print("\n" + "=" * 100)
    print("🎉 【交叉验证最终结果】")
    print("=" * 100)
    print(f"✅ 平均准确率：{np.mean(all_test_acc):.4f}")
    print(f"✅ 平均精确率：{np.mean(all_test_prec):.4f}")
    print(f"✅ 平均召回率：{np.mean(all_test_recall):.4f}")
    print(f"✅ 平均F1分数：{np.mean(all_test_f1):.4f}")
    print("=" * 100)

if __name__ == "__main__":
    main()