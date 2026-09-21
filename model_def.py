import torch
import torch.nn as nn
from config import *

class AutoEncoder(nn.Module):
    def __init__(self, input_dim=FEATURE_DIM):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 64), nn.ReLU(),
            nn.Linear(64, 32), nn.ReLU(),
            nn.Linear(32, 16)
        )
        self.decoder = nn.Sequential(
            nn.Linear(16, 32), nn.ReLU(),
            nn.Linear(32, 64), nn.ReLU(),
            nn.Linear(64, input_dim)
        )

    def forward(self, x):
        feat = self.encoder(x)
        recon_x = self.decoder(feat)
        return recon_x, feat

class MILHead(nn.Module):
    def __init__(self, feat_dim=16):
        super().__init__()
        self.instance_cls = nn.Sequential(
            nn.Linear(feat_dim, 32), nn.ReLU(),
            nn.Linear(32, 1), nn.Sigmoid()
        )

    def forward(self, feat):
        instance_probs = self.instance_cls(feat)
        bag_prob = torch.max(instance_probs)
        return bag_prob

class AEMILPU(nn.Module):
    def __init__(self):
        super().__init__()
        self.ae = AutoEncoder()
        self.mil = MILHead()

    def forward(self, x):
        x = torch.tensor(x, dtype=torch.float32).to(DEVICE)
        recon_x, feat = self.ae(x)
        recon_loss = nn.MSELoss()(recon_x, x)
        bag_prob = self.mil(feat)
        return recon_loss, bag_prob

    def pu_loss(self, bag_prob, label):
        label = label.item()
        if label == 1:
            return -torch.log(bag_prob + 1e-8)
        else:
            return -torch.log(1 - bag_prob + 1e-8) * 0.1

class TrafficDataset(torch.utils.data.Dataset):
    def __init__(self, bags, labels):
        self.bags = bags
        self.labels = labels

    def __len__(self):
        return len(self.bags)

    def __getitem__(self, idx):
        return self.bags[idx], self.labels[idx]