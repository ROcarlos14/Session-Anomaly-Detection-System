import os
import sys
import time
import logging
import itertools
import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trainer import generate_normal_traffic, generate_anomalous_traffic, _gen_with_rng
from feature_extractor import FeatureExtractor, N_FEATURES
from models.isolation_forest import IsolationForestDetector
from models.lstm_model import LSTMDetector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] TUNER: %(message)s")
logger = logging.getLogger("tuner")
SAVED_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_models")
os.makedirs(SAVED_MODELS_DIR, exist_ok=True)

RNG = np.random.default_rng(9999)

def generate_datasets():
    logger.info("Generating massive tuning datasets (100k normal, 10k anomaly)...")
    X_train_normal = generate_normal_traffic(100000)
    
    test_rng = np.random.default_rng(777)
    X_test_normal = _gen_with_rng(generate_normal_traffic, 10000, test_rng)
    X_test_anom = _gen_with_rng(generate_anomalous_traffic, 10000, test_rng)
    
    fe = FeatureExtractor()
    fe.fit_scaler(X_train_normal)
    
    X_train_scaled = fe.transform(X_train_normal)
    
    X_test = np.vstack([X_test_normal, X_test_anom])
    y_test = np.array([0]*10000 + [1]*10000, dtype=np.int32)
    X_test_scaled = fe.transform(X_test)
    
    return X_train_scaled, X_test_scaled, y_test, fe

def tune_isolation_forest(X_train, X_test, y_test):
    logger.info("Starting Isolation Forest hyperparameter search...")
    param_grid = {
        'n_estimators': [100, 200, 300, 500],
        'contamination': [0.01, 0.05, 0.1]
    }
    
    keys, values = zip(*param_grid.items())
    permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    best_f1 = 0
    best_model = None
    best_params = None
    
    for params in permutations:
        logger.info(f"Testing IF with: {params}")
        model = IsolationForestDetector(**params, random_state=42)
        model.train(X_train)
        
        scores, preds = model.predict(X_test)
        f1 = f1_score(y_test, preds, zero_division=0)
        p = precision_score(y_test, preds, zero_division=0)
        logger.info(f"Result -> F1: {f1:.4f}, Precision: {p:.4f}")
        
        if f1 > best_f1:
            best_f1 = f1
            best_model = model
            best_params = params
            
    logger.info(f"Best Isolation Forest F1: {best_f1:.4f} with {best_params}")
    return best_model

def tune_lstm(X_train, X_test, y_test):
    logger.info("Starting LSTM Autoencoder hyperparameter search...")
    param_grid = {
        'sequence_length': [10, 20],
        'lstm_units': [32, 64],
        'epochs': [20, 50],
        'batch_size': [64, 128]
    }
    
    keys, values = zip(*param_grid.items())
    permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    best_f1 = 0
    best_model = None
    best_params = None
    
    for params in permutations:
        logger.info(f"Testing LSTM with: {params}")
        # Build model with specific sequence length and units
        model = LSTMDetector(sequence_length=params['sequence_length'], n_features=N_FEATURES, lstm_units=params['lstm_units'])
        
        try:
            model.train(X_train, epochs=params['epochs'], batch_size=params['batch_size'], verbose=0)
            scores, preds = model.predict(X_test)
            f1 = f1_score(y_test, preds, zero_division=0)
            p = precision_score(y_test, preds, zero_division=0)
            logger.info(f"Result -> F1: {f1:.4f}, Precision: {p:.4f}")
            
            if f1 > best_f1:
                best_f1 = f1
                best_model = model
                best_params = params
        except Exception as e:
            logger.error(f"Failed combination {params}: {e}")
            
    logger.info(f"Best LSTM Autoencoder F1: {best_f1:.4f} with {best_params}")
    return best_model

def main():
    start_time = time.time()
    target_duration = 2 * 3600  # 2 hours
    logger.info("Starting 2-hour autonomous ML optimization goal...")
    
    X_train, X_test, y_test, fe = generate_datasets()
    
    # Run optimization loops
    best_if = tune_isolation_forest(X_train, X_test, y_test)
    best_lstm = tune_lstm(X_train, X_test, y_test)
    
    # Save the absolute best models
    logger.info(f"Optimization complete. Saving best models to {SAVED_MODELS_DIR}")
    best_if.save(os.path.join(SAVED_MODELS_DIR, "isolation_forest.joblib"))
    best_lstm.save(os.path.join(SAVED_MODELS_DIR, "lstm_autoencoder.keras"))
    fe.save(os.path.join(SAVED_MODELS_DIR, "feature_scaler.joblib"))
    
    logger.info("Writing tuning report...")
    with open(os.path.join(SAVED_MODELS_DIR, "tuning_report.txt"), "w") as f:
        f.write(f"Optimization finished in {time.time() - start_time:.1f} seconds.\n")
        f.write("Best models have been saved.\n")
        
    logger.info("Goal Achieved!")

if __name__ == "__main__":
    main()
