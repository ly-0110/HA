# 全局配置参数
import torch
import os
import warnings
warnings.filterwarnings("ignore")  # 关掉烦人的警告

# ===================== 核心路径配置 =====================
# 【正确 Windows 路径写法：前面加 r】
JSON_LOG_PATH = r"D:\PythonCode\HA\ha_data\ha_device_log_light_yeelink.json"
PCAP_RAW_PATH = r"D:\PythonCode\HA\预处理数据\new_pre_light.pcapng"
ALIGNED_PCAP_DIR = os.path.dirname(PCAP_RAW_PATH)

# ===================== 设备/时间配置 =====================
TARGET_DEVICE_IP = "192.168.1.207"
LOG_DELAY = 2
TIME_WINDOW = 10
TIME_WINDOW_HALF = 5

# ===================== 模型/训练配置 =====================
FEATURE_DIM = 7
EPOCHS = 50
LR = 1e-3
THRESHOLD = 0.5
DEVICE = torch.device("cpu")
SEED = 42

TEST_SPLIT = 0.1
VAL_SPLIT = 0.1
TRAIN_SPLIT = 0.8

POS_BAG_PCAP_NAME = "aligned_pos_bags.pcapng"
UNLABELED_PCAP_NAME = "aligned_unlabeled_bags.pcapng"
BEST_MODEL_NAME = "aemilpu_best_model.pth"
SCALER_MEAN_NAME = "scaler_mean.npy"
SCALER_SCALE_NAME = "scaler_scale.npy"