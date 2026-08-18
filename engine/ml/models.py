"""
머신러닝 / 딥러닝 모델 모듈
===========================
지원 모델
---------
- rf          : Random Forest (분류/회귀)
- xgb         : XGBoost       (분류/회귀)  - xgboost 패키지 필요
- lstm        : LSTM 시퀀스 모델 (PyTorch)
- gru         : GRU 시퀀스 모델
- transformer : Transformer Encoder

공통 인터페이스
---------------
>>> tr = Trainer(model_type='lstm', task='classification')
>>> tr.fit(feature_df)
>>> proba = tr.predict(feature_df)
>>> metrics = tr.evaluate(feature_df)
>>> shap = tr.shap_values(feature_df)   # tree 계열만

속성 설명
---------
- model_type    : 모델 종류
- task          : 'classification' or 'regression'
- seq_len       : 시퀀스 모델 입력 길이
- epochs        : 딥러닝 학습 에폭
- batch_size    : 배치 크기
- lr            : 학습률
- device        : 'cuda' / 'cpu' (자동)
- scaler        : StandardScaler (피처 정규화)
- feature_cols_ : 학습에 사용한 컬럼 목록
"""
from __future__ import annotations
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    import torch
    import torch.nn as nn
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    import shap
    HAS_SHAP = True
except ImportError:
    HAS_SHAP = False


# ------------------------------------------------------------------ #
#  PyTorch 시퀀스 모델 정의
# ------------------------------------------------------------------ #
if HAS_TORCH:

    class LSTMModel(nn.Module):
        """LSTM 기반 시계열 모델."""
        def __init__(self, n_features, hidden=64, n_layers=2, n_out=1):
            super().__init__()
            self.lstm = nn.LSTM(n_features, hidden, n_layers,
                                batch_first=True, dropout=0.1)
            self.fc = nn.Linear(hidden, n_out)

        def forward(self, x):
            o, _ = self.lstm(x)
            return self.fc(o[:, -1, :])

    class GRUModel(nn.Module):
        """GRU 기반 — LSTM 보다 파라미터 적고 빠름."""
        def __init__(self, n_features, hidden=64, n_layers=2, n_out=1):
            super().__init__()
            self.gru = nn.GRU(n_features, hidden, n_layers,
                              batch_first=True, dropout=0.1)
            self.fc = nn.Linear(hidden, n_out)

        def forward(self, x):
            o, _ = self.gru(x)
            return self.fc(o[:, -1, :])

    class TransformerModel(nn.Module):
        """Transformer Encoder 기반 시계열 모델."""
        def __init__(self, n_features, d_model=64, n_heads=4,
                     n_layers=2, n_out=1):
            super().__init__()
            self.proj = nn.Linear(n_features, d_model)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=n_heads,
                dim_feedforward=128, dropout=0.1, batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(enc_layer, num_layers=n_layers)
            self.fc = nn.Linear(d_model, n_out)

        def forward(self, x):
            x = self.proj(x)
            x = self.encoder(x)
            return self.fc(x[:, -1, :])


def _make_sequences(X: np.ndarray, y: np.ndarray, seq_len: int):
    """슬라이딩 윈도 방식의 시퀀스 데이터셋 생성."""
    Xs, ys = [], []
    for i in range(len(X) - seq_len):
        Xs.append(X[i : i + seq_len])
        ys.append(y[i + seq_len])
    return np.asarray(Xs, dtype=np.float32), np.asarray(ys, dtype=np.float32)


# ------------------------------------------------------------------ #
#  통합 Trainer
# ------------------------------------------------------------------ #
class Trainer:
    """
    모든 ML 모델을 같은 인터페이스로 사용.

    Parameters
    ----------
    model_type    : 'rf' | 'xgb' | 'lstm' | 'gru' | 'transformer'
    task          : 'classification' | 'regression'
    seq_len       : 시퀀스 모델 입력 길이 (LSTM/GRU/TF 만 사용)
    epochs        : 딥러닝 에폭
    batch_size    : 배치 크기
    lr            : 학습률
    device        : 'cuda' / 'cpu' (None=자동)
    random_state  : 재현성 시드
    """

    SEQUENCE_MODELS = {"lstm", "gru", "transformer"}

    def __init__(self,
                 model_type: str = "rf",
                 task: str = "classification",
                 seq_len: int = 30,
                 epochs: int = 20,
                 batch_size: int = 64,
                 lr: float = 1e-3,
                 device: Optional[str] = None,
                 random_state: int = 42):
        self.model_type   = model_type.lower()
        self.task         = task.lower()
        self.seq_len      = seq_len
        self.epochs       = epochs
        self.batch_size   = batch_size
        self.lr           = lr
        self.random_state = random_state
        self.device = device or (
            "cuda" if HAS_TORCH and torch.cuda.is_available() else "cpu"
        )
        self.scaler = StandardScaler()
        self.model = None
        self.feature_cols_: list = []

    # ------------------------------------------------------------- #
    def _build_torch(self, n_features: int):
        if self.model_type == "lstm":
            return LSTMModel(n_features, n_out=1).to(self.device)
        if self.model_type == "gru":
            return GRUModel(n_features, n_out=1).to(self.device)
        if self.model_type == "transformer":
            return TransformerModel(n_features, n_out=1).to(self.device)
        raise ValueError(self.model_type)

    def _split_xy(self, df: pd.DataFrame, target_col: str):
        y = df[target_col].values.astype(np.float32)
        feats = [c for c in df.columns if c not in {"target", "target_reg"}]
        self.feature_cols_ = feats
        X = df[feats].values.astype(np.float32)
        return X, y

    # ------------------------------------------------------------- #
    def fit(self, df: pd.DataFrame, target_col: str = "target") -> "Trainer":
        """모델 학습. df 는 make_features 결과."""
        if self.task == "regression" and target_col == "target":
            target_col = "target_reg"

        X, y = self._split_xy(df, target_col)
        X = self.scaler.fit_transform(X)

        if self.model_type == "rf":
            cls = (RandomForestClassifier if self.task == "classification"
                   else RandomForestRegressor)
            self.model = cls(n_estimators=300,
                             random_state=self.random_state, n_jobs=-1)
            self.model.fit(X, y)

        elif self.model_type == "xgb":
            if not HAS_XGB:
                raise ImportError("xgboost 가 설치되지 않았습니다")
            cls = (xgb.XGBClassifier if self.task == "classification"
                   else xgb.XGBRegressor)
            self.model = cls(
                n_estimators=400, max_depth=5, learning_rate=0.05,
                random_state=self.random_state, n_jobs=-1,
                eval_metric=("logloss" if self.task == "classification" else "rmse"),
            )
            self.model.fit(X, y)

        elif self.model_type in self.SEQUENCE_MODELS:
            if not HAS_TORCH:
                raise ImportError("PyTorch 가 설치되지 않았습니다")
            Xs, ys = _make_sequences(X, y, self.seq_len)
            if len(Xs) == 0:
                raise ValueError("시퀀스 데이터가 너무 짧습니다")
            self.model = self._build_torch(X.shape[1])
            opt = torch.optim.Adam(self.model.parameters(), lr=self.lr)
            loss_fn = (nn.BCEWithLogitsLoss() if self.task == "classification"
                       else nn.MSELoss())

            Xt = torch.tensor(Xs).to(self.device)
            yt = torch.tensor(ys).unsqueeze(-1).to(self.device)

            self.model.train()
            for ep in range(self.epochs):
                idx = np.random.permutation(len(Xt))
                for i in range(0, len(idx), self.batch_size):
                    b = idx[i : i + self.batch_size]
                    opt.zero_grad()
                    out = self.model(Xt[b])
                    loss = loss_fn(out, yt[b])
                    loss.backward()
                    opt.step()
        else:
            raise ValueError(f"미지원 모델 : {self.model_type}")

        return self

    # ------------------------------------------------------------- #
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """예측 (분류는 확률, 회귀는 수치)."""
        feats = [c for c in self.feature_cols_ if c in df.columns]
        X = self.scaler.transform(df[feats].values.astype(np.float32))

        if self.model_type in {"rf", "xgb"}:
            if self.task == "classification":
                if hasattr(self.model, "predict_proba"):
                    return self.model.predict_proba(X)[:, 1]
                return self.model.predict(X)
            return self.model.predict(X)

        if self.model_type in self.SEQUENCE_MODELS:
            self.model.eval()
            Xs, _ = _make_sequences(X, np.zeros(len(X)), self.seq_len)
            if len(Xs) == 0:
                return np.array([])
            with torch.no_grad():
                Xt = torch.tensor(Xs).to(self.device)
                out = self.model(Xt).cpu().numpy().ravel()
            if self.task == "classification":
                out = 1 / (1 + np.exp(-out))
            return np.concatenate([np.full(self.seq_len, np.nan), out])

        raise RuntimeError("모델이 학습되지 않았습니다")

    # ------------------------------------------------------------- #
    def evaluate(self, df: pd.DataFrame, target_col: str = "target") -> Dict[str, float]:
        """holdout 평가 지표."""
        if self.task == "regression" and target_col == "target":
            target_col = "target_reg"
        y = df[target_col].values
        pred = self.predict(df)

        L = min(len(y), len(pred))
        y, pred = y[-L:], pred[-L:]
        m = ~np.isnan(pred)
        y, pred = y[m], pred[m]
        if len(y) == 0:
            return {}

        if self.task == "classification":
            return {
                "accuracy": float(accuracy_score(y, (pred > 0.5).astype(int))),
                "n":        int(len(y)),
            }
        return {
            "mae":  float(mean_absolute_error(y, pred)),
            "mse":  float(mean_squared_error(y, pred)),
            "rmse": float(np.sqrt(mean_squared_error(y, pred))),
            "n":    int(len(y)),
        }

    # ------------------------------------------------------------- #
    def shap_values(self, df: pd.DataFrame, sample: int = 200) -> Optional[Dict[str, float]]:
        """SHAP 피처 중요도 (tree 모델만 지원)."""
        if not HAS_SHAP or self.model_type not in {"rf", "xgb"}:
            return None
        X = self.scaler.transform(df[self.feature_cols_].values.astype(np.float32))
        X = X[: min(sample, len(X))]
        explainer = shap.TreeExplainer(self.model)
        sv = explainer.shap_values(X)
        if isinstance(sv, list):
            sv = sv[1]
        importance = np.abs(sv).mean(axis=0)
        return dict(zip(self.feature_cols_, importance.tolist()))
