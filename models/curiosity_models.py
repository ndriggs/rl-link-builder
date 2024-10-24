import torch
import torch.nn as nn
import lightning as pl
from torch.optim.lr_scheduler import ExponentialLR, LambdaLR
from reformer_pytorch import ReformerLM
from torch_geometric.nn import GATConv
import numpy as np
import math

class MLP(pl.LightningModule):
    def __init__(self, lk_matrix_size, hidden_size, 
                 dropout, num_invariants=1, classification=False, num_classes=41):
        super(MLP, self).__init__()

        self.classification = classification
        self.num_classes = num_classes

        self.mse_loss = nn.MSELoss()
        self.l1_loss = nn.L1Loss()
        if classification :
            self.cross_entropy = nn.CrossEntropyLoss(label_smoothing=0.1)

        self.fc1 = nn.Linear(lk_matrix_size**2, hidden_size)
        self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.fc3 = nn.Linear(hidden_size, num_invariants)

        self.dropout = nn.Dropout(dropout)


    def forward(self, x) :
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        x = F.relu(self.fc2(x))
        x = self.dropout(x)
        x = self.fc3(x)

        return x

    def class_idx_to_sig(idx) :
        max_min_sig = (self.num_classes-1)/2
        idx[idx > max_min_sig] = -1*(idx[idx>max_min_sig] - max_min_sig)
        return idx

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            loss = self.cross_entropy(y_hat, y)
        else : # regression
            loss = self.mse_loss(y_hat, y)
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('val_mse_loss', mse_loss)
        self.log('val_l1_loss', l1_loss)

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('test_mse_loss', mse_loss)
        self.log('test_l1_loss', l1_loss)

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=.001)
        
        scheduler = ExponentialLR(optimizer, gamma=0.95)
        
        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": scheduler,
                "interval": "epoch",
                "frequency": 1,
                "name": "exp_lr"
            }
        }


class CNN(pl.LightningModule):
    def __init__(self, lk_matrix_size: int, kernel_size: int, 
                 layer_norm: bool, num_invariants: int = 1,
                 classification=False, num_classes=41) :
        super(CNN, self).__init__()

        self.layer_norm = layer_norm
        self.classification = classification
        self.num_classes = num_classes

        self.mse_loss = nn.MSELoss()
        self.l1_loss = nn.L1Loss()
        if classification :
            self.cross_entropy = nn.CrossEntropyLoss(label_smoothing=0.1)

        self.relu = nn.ReLU()

        padding = int(kernel_size == 3)
        self.conv1 = nn.Conv2d(in_channels=1, out_channels=16,
                              kernel_size=kernel_size, stride=1,
                              padding=padding)
        self.conv2 = nn.Conv2d(in_channels=16, out_channels=32,
                              kernel_size=kernel_size, stride=1,
                              padding=padding)
        self.conv3 = nn.Conv2d(in_channels=32, out_channels=64,
                              kernel_size=kernel_size, stride=1,
                              padding=padding)

        if layer_norm :
          self.norm1 = nn.LayerNorm([16, lk_matrix_size-(3-kernel_size),
                                    lk_matrix_size-(3-kernel_size)])
          self.norm2 = nn.LayerNorm([32, lk_matrix_size-2*(3-kernel_size),
                                    lk_matrix_size-2*(3-kernel_size)])
          self.norm3 = nn.LayerNorm([64, lk_matrix_size-3*(3-kernel_size),
                                    lk_matrix_size-3*(3-kernel_size)])

        self.fc1 = nn.Linear(64*(lk_matrix_size-3*(3-kernel_size))**2, 1000)
        if self.classification :
            self.fc2 = nn.Linear(1000, num_classes)
        else : 
            self.fc2 = nn.Linear(1000, num_invariants)

    def forward(self, x) :
        # first convolution layer
        x = self.conv1(x)
        if self.layer_norm : 
          x = self.norm1(x)
        x = self.relu(x)

        # second convolutional layer 
        x = self.conv2(x)
        if self.layer_norm :
          x = self.norm2(x)
        x = self.relu(x)

        # third convolutional layer 
        x = self.conv3(x)
        if self.layer_norm : 
          x = self.norm3(x) 
        x = self.relu(x)
        
        # feed forward layers
        x = x.view(x.shape[0],-1)
        x = self.relu(self.fc1(x))
        x = self.fc2(x)

        return x

    def class_idx_to_sig(idx) :
        max_min_sig = (self.num_classes-1)/2
        idx[idx > max_min_sig] = -1*(idx[idx>max_min_sig] - max_min_sig)
        return idx

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            loss = self.cross_entropy(y_hat, y)
        else : # regression
            loss = self.mse_loss(y_hat, y)
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('val_mse_loss', mse_loss)
        self.log('val_l1_loss', l1_loss)

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('test_mse_loss', mse_loss)
        self.log('test_l1_loss', l1_loss)

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=.001)
        scheduler = ExponentialLR(optimizer, gamma=0.95)
        
        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": scheduler,
                "interval": "epoch",
                "frequency": 1,
                "name": = "exp_lr"
            }
        }


class TransformerEncoder(pl.LightningModule):
    def __init__(self, vocab_size, d_model, nhead, num_encoder_layers, 
                 dim_feedforward, max_seq_length, classification=False,
                 num_classes=41):
        super(TransformerEncoder, self).__init__()

        self.classification = classification
        self.num_classes = num_classes

        self.mse_loss = nn.MSELoss()
        self.l1_loss = nn.L1Loss()
        if classification :
            self.cross_entropy = nn.CrossEntropyLoss(label_smoothing=0.1)
        
        self.d_model = d_model
        self.embed = nn.Embedding(vocab_size, d_model)
        self.pos_encoder = PositionalEncoding(d_model, max_seq_length)
        
        encoder_layers = nn.TransformerEncoderLayer(d_model, nhead, dim_feedforward)
        self.transformer_encoder = nn.TransformerEncoder(encoder_layers, num_encoder_layers)
        
        if self.classification :
            self.final_layer = nn.Linear(d_model, num_classes)
        else : # regression
            self.final_layer = nn.Linear(d_model, 1)

    def forward(self, src):
        # src shape: (seq_len, batch_size)
        
        # Embed the input tokens
        src = self.embed(src) * math.sqrt(self.d_model)
        
        # Add positional encoding
        src = self.pos_encoder(src)
        
        # Pass through the transformer encoder
        output = self.transformer_encoder(src)
        
        # Take the mean of the sequence dimension
        output = output.mean(dim=0)
        
        # Pass through the final linear layer
        output = self.final_layer(output)
        
        # Squeeze to get a single value
        return output.squeeze(-1)

    def class_idx_to_sig(idx) :
        max_min_sig = (self.num_classes-1)/2
        idx[idx > max_min_sig] = -1*(idx[idx>max_min_sig] - max_min_sig)
        return idx

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            loss = self.cross_entropy(y_hat, y)
        else : # regression
            loss = self.mse_loss(y_hat, y)
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('val_mse_loss', mse_loss)
        self.log('val_l1_loss', l1_loss)

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('test_mse_loss', mse_loss)
        self.log('test_l1_loss', l1_loss)

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=.001)

        def noam_lambda(step):
            return (self.d_model ** -0.5) * min((step + 1) ** -0.5, (step + 1) * self.warmup_steps ** -1.5)

        scheduler = LambdaLR(optimizer, lr_lambda=noam_lambda)
        
        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": scheduler,
                "interval": "step",
                "frequency": 1,
                "name": "noam_lr"
            }
        }

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len):
        super(PositionalEncoding, self).__init__()
        
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        
        pe = pe.unsqueeze(0).transpose(0, 1)
        self.register_buffer('pe', pe)

    def forward(self, x):
        return x + self.pe[:x.size(0), :]

class Reformer(pl.LightningModule) :
    def __init__(self, vocab_size, d_model, nhead, num_layers
                 max_seq_len, classification=False,
                 num_classes=41):
        super(Reformer, self).__init__()

        self.classification = classification
        self.num_classes = num_classes
        self.d_model = d_model

        self.mse_loss = nn.MSELoss()
        self.l1_loss = nn.L1Loss()
        if classification :
            self.cross_entropy = nn.CrossEntropyLoss(label_smoothing=0.1)

        self.reformer = ReformerLM(vocab_size, d_model, num_layers, max_seq_len=max_seq_len, 
                                   heads = nhead, causal = False, use_full_attn = True,  
                                   return_embeddings = not classification, axial_position_emb = True)

        if not self.classification : 
            self.fc = nn.Linear(d_model, 1)

    def forward(self, x) :
        x = self.reformer(x)
        if not self.classification :
            x = self.fc(x)
        return x

    def class_idx_to_sig(idx) :
        max_min_sig = (self.num_classes-1)/2
        idx[idx > max_min_sig] = -1*(idx[idx>max_min_sig] - max_min_sig)
        return idx

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            loss = self.cross_entropy(y_hat, y)
        else : # regression
            loss = self.mse_loss(y_hat, y)
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('val_mse_loss', mse_loss)
        self.log('val_l1_loss', l1_loss)

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('test_mse_loss', mse_loss)
        self.log('test_l1_loss', l1_loss)

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=.001)

        def noam_lambda(step):
            return (self.d_model ** -0.5) * min((step + 1) ** -0.5, (step + 1) * self.warmup_steps ** -1.5)

        scheduler = LambdaLR(optimizer, lr_lambda=noam_lambda)
        
        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": scheduler,
                "interval": "step",
                "frequency": 1,
                "name": "noam_lr"
            }
        }


class GNN(plt.LightningModule):
    def __init__(self, hidden_channels, num_heads=2, num_layers=2, classification=False, num_classes=41):
        super(GNN, self).__init__()
        self.gat1 = GATConv(1, hidden_channels, heads=num_heads)
        self.gat2 = GATConv(hidden_channels * num_heads, hidden_channels, heads=num_heads)
        if num_layers == 3 :
            self.gat3 = GATConv(hidden_channels*num_heads, hidden_channels, heads=num_heads)
        if classification :
            self.fc = torch.nn.Linear(hidden_channels * num_heads, num_classes)
        else :
            self.fc = torch.nn.Linear(hidden_channels * num_heads, 1)

    def forward(self, x, edge_index):
        # first GAT layer 
        x = self.gat1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, p=0.3, training=self.training)
        
        # second GAT layer
        x = self.gat2(x, edge_index)
        x = F.relu(x)

        # (optional) third GAT layer
        if num_layers == 3 :
            x = F.dropout(x, p=0.3, training=self.training)
            x = self.gat3(x, edge_index)
            x = F.relu(x)
        
        # pooling and linear layer
        x = torch.mean(x, dim=0).unsqueeze(0) 
        x = self.fc(x)
        
        return x

    def class_idx_to_sig(idx) :
        max_min_sig = (self.num_classes-1)/2
        idx[idx > max_min_sig] = -1*(idx[idx>max_min_sig] - max_min_sig)
        return idx

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            loss = self.cross_entropy(y_hat, y)
        else : # regression
            loss = self.mse_loss(y_hat, y)
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('val_mse_loss', mse_loss)
        self.log('val_l1_loss', l1_loss)

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        if self.classification :
            assert len(y_hat.shape) == 2
            class_idxs = y_hat.argmax(1) # preserve the batch dim, armax over classes
            y_hat = class_idx_to_sig(class_idxs).unsqueeze(1)
            y = class_idx_to_sig(y.to(torch.float32))
        mse_loss = self.mse_loss(y_hat, y)
        l1_loss = self.l1_loss(y_hat, y)
        self.log('test_mse_loss', mse_loss)
        self.log('test_l1_loss', l1_loss)

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=.001)
        scheduler = ExponentialLR(optimizer, gamma=0.95)
        
        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": scheduler,
                "interval": "epoch",
                "frequency": 1,
                "name": "exp_lr"
            }
        }
        