"""the master's autoencoder, rebuilt so the board can run it.

the deployed ae_int8.tflite runs on the ESP32-S3 under TFLite Micro (bundled
with the Arduino core 3.3.x, ESP-NN kernels) but takes 13.5 s a window: its
four TRANSPOSE_CONV layers have no ESP-NN kernel. this rewrites the same
weights into layers that do, with the same float math, no retraining:

    Conv1DTranspose(k=7, stride 2, same)
        = even outputs: Conv1D(5 taps: W6 W4 W2 W0 0, same)
        + odd outputs:  Conv1D(3 taps: W5 W3 W1, same)
        interleaved by Concatenate(channels) + Reshape(2L, C)
    Dense  = 1x1 Conv1D

measured on a Rev F board (2026-11-11): 256 ms a window with the model copied
into PSRAM, output bit-exact with TFLite's reference kernels on the PC. float
outputs match the original to <1e-6; re-quantised to int8, pooled WESAD PR-AUC
0.9019 vs the deployed model's 0.9022, scores rank-correlated 0.99995.

writes:
    anomaly/saved/ae_int8_board.tflite   the rewritten int8 model (4.2 MB)
    pulse_headless/ae_model.h            GENERATED, gitignored (14 MB of C):
                                         the model bytes, its io quantisation,
                                         and a self-test window with the
                                         reference kernels' exact output

    python -m anomaly.board_ae_export            # needs anomaly/saved/ae.keras + the WESAD cache
    python -m anomaly.board_ae_export --check    # also score every WESAD window both ways (~5 min)
"""
from __future__ import annotations

import argparse
import os
from collections import Counter

import numpy as np

from .infer import SAVE_DIR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_TFLITE = os.path.join(SAVE_DIR, "ae_int8_board.tflite")
OUT_HEADER = os.path.join(ROOT, "pulse_headless", "ae_model.h")
OPS_ALLOWED = {"CONV_2D", "RESHAPE", "CONCATENATION", "ADD"}   # what the sketch registers


def _z(X):
    return ((X - X.mean(1, keepdims=True)) / (X.std(1, keepdims=True) + 1e-8)).astype(np.float32)


def rewrite(src):
    """the same network with every transpose conv and dense layer swapped for
    ordinary convolutions. batch fixed at 1, so no dynamic-shape ops."""
    import tensorflow as tf
    from tensorflow.keras import layers

    inp = layers.Input(batch_shape=(1,) + tuple(src.input_shape[1:]))
    x = inp
    for l in src.layers:
        if isinstance(l, layers.InputLayer):
            continue
        if isinstance(l, layers.Conv1DTranspose):
            W, b = l.get_weights()                      # (7, out, in)
            if W.shape[0] != 7 or tuple(l.strides) != (2,) or l.padding != "same":
                raise ValueError(f"{l.name}: only k=7, stride 2, same is handled")
            cout, cin = W.shape[1], W.shape[2]
            T = lambda k: W[k].T                         # (in, out)
            Ve = np.stack([T(6), T(4), T(2), T(0), np.zeros((cin, cout), np.float32)])
            Vo = np.stack([T(5), T(3), T(1)])
            ce = layers.Conv1D(cout, 5, padding="same", activation=l.activation)
            co = layers.Conv1D(cout, 3, padding="same", activation=l.activation)
            e, o = ce(x), co(x)
            ce.set_weights([Ve, b])
            co.set_weights([Vo, b])
            x = layers.Reshape((2 * x.shape[1], cout))(layers.Concatenate(axis=-1)([e, o]))
        elif isinstance(l, layers.Dense):
            Wd, bd = l.get_weights()
            c = layers.Conv1D(Wd.shape[1], 1, activation=l.activation)
            x = layers.Reshape((1, Wd.shape[0]))(x)
            x = c(x)
            c.set_weights([Wd[None], bd])
            x = layers.Reshape((Wd.shape[1],))(x)
        else:
            cfg = l.get_config()
            cfg.pop("name", None)
            nl = l.__class__.from_config(cfg)
            x = nl(x)
            if l.get_weights():
                nl.set_weights(l.get_weights())
    return tf.keras.Model(inp, x)


def _scores(blob, X, ref=False):
    """reconstruction MSE per window, as anomaly.infer scores it; plus the raw
    int8 input/output of the first window."""
    import tensorflow as tf
    kw = {}
    if ref:      # TFLite's reference kernels: what TFLite Micro computes
        kw = dict(experimental_op_resolver_type=tf.lite.experimental.OpResolverType.BUILTIN_REF)
    it = tf.lite.Interpreter(model_content=blob, **kw)
    it.allocate_tensors()
    i, o = it.get_input_details()[0], it.get_output_details()[0]
    s, zp = i["quantization"]
    so, zo = o["quantization"]
    out = np.empty(len(X))
    first = None
    for k, x in enumerate(X):
        q = np.clip(np.round(x / s + zp), -128, 127).astype(np.int8)[None, :, None]
        it.set_tensor(i["index"], q)
        it.invoke()
        r = it.get_tensor(o["index"])
        if k == 0:
            first = (q.reshape(-1).copy(), r.reshape(-1).copy())
        out[k] = np.mean(((r.astype(np.float32) - zo) * so - x[None, :, None]) ** 2)
    return out, first, (float(s), int(zp), float(so), int(zo))


def _carr(name, data, ctype, per=48):
    rows = [",".join(str(int(v)) for v in data[i:i + per]) for i in range(0, len(data), per)]
    return f"alignas(16) static const {ctype} {name}[{len(data)}] = {{\n" + ",\n".join(rows) + "\n};\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="score every WESAD window with both models (~5 min)")
    args = ap.parse_args()

    import tensorflow as tf
    from .run import load_all, NORMAL, POSITIVE
    from .wesad import SUBJECTS

    src = tf.keras.models.load_model(os.path.join(SAVE_DIR, "ae.keras"), compile=False)
    m = rewrite(src)
    t = np.random.default_rng(0).standard_normal((1,) + tuple(src.input_shape[1:])).astype(np.float32)
    d = float(np.max(np.abs(m(t).numpy() - src(t).numpy())))
    print(f"float: max |rewrite - original| = {d:.2g}")
    if d > 1e-4:
        raise SystemExit("the rewrite does not reproduce the original network")

    # the same representative set anomaly.compress quantised the deployed model with
    data = load_all(SUBJECTS, 60, 5)
    calm = np.concatenate([data[s][0][np.isin(data[s][1], list(NORMAL))] for s in data])
    rep_pool = _z(calm)

    def rep():
        for r in rep_pool[:200]:
            yield [r[None, :, None]]

    c = tf.lite.TFLiteConverter.from_keras_model(m)
    c.optimizations = [tf.lite.Optimize.DEFAULT]
    c.representative_dataset = rep
    c.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    c.inference_input_type = tf.int8
    c.inference_output_type = tf.int8
    blob = c.convert()

    it = tf.lite.Interpreter(model_content=blob)
    it.allocate_tensors()
    ops = Counter(o["op_name"] for o in it._get_ops_details() if o["op_name"] != "DELEGATE")
    print(f"int8: {len(blob):,} bytes, ops {dict(ops)}")
    if set(ops) - OPS_ALLOWED:
        raise SystemExit(f"ops the sketch does not register: {set(ops) - OPS_ALLOWED}")

    # self-test window: the first calm window, scored with the reference kernels
    s_ref, (q_in, q_out), (s, zp, so, zo) = _scores(blob, rep_pool[:1], ref=True)
    print(f"self-test window score {s_ref[0]:.6f}  io quant in ({s:.9g}, {zp}) out ({so:.9g}, {zo})")

    if args.check:
        from scipy.stats import spearmanr
        from .metrics import pr_auc, recall_at_specificity
        stress = np.concatenate([data[k][0][np.isin(data[k][1], list(POSITIVE))] for k in data])
        Xz = _z(np.vstack([calm, stress]))
        y = np.r_[np.zeros(len(calm)), np.ones(len(stress))].astype(int)
        dep = open(os.path.join(SAVE_DIR, "ae_int8.tflite"), "rb").read()
        s_dep, _, _ = _scores(dep, Xz)
        s_new, _, _ = _scores(blob, Xz)
        for n, sc in (("deployed ae_int8", s_dep), ("board rewrite", s_new)):
            print(f"  {n:18s} PR-AUC {pr_auc(y, sc):.4f}  recall@90 {recall_at_specificity(y, sc):.4f}")
        print(f"  rank correlation {spearmanr(s_new, s_dep)[0]:.5f}")

    with open(OUT_TFLITE, "wb") as f:
        f.write(blob)
    tag = f"ae-{len(blob)}-{int(np.sum(np.frombuffer(blob, np.uint8), dtype=np.uint64)) & 0xffffff:06x}"
    with open(OUT_HEADER, "w") as f:
        f.write("// GENERATED by `python -m anomaly.board_ae_export` -- do not edit, not committed.\n")
        f.write("// the master's autoencoder with its transpose convs and dense layers rewritten\n")
        f.write("// as plain convolutions (same math), int8, for TFLite Micro + ESP-NN.\n")
        f.write("#pragma once\n#include <stdint.h>\n\n")
        f.write(f'#define AE_MODEL_TAG "{tag}"\n')
        f.write(f"#define AE_WIN {q_in.size}\n")
        f.write(f"#define AE_IN_SCALE {s:.9g}f\n#define AE_IN_ZP {zp}\n")
        f.write(f"#define AE_OUT_SCALE {so:.9g}f\n#define AE_OUT_ZP {zo}\n")
        f.write(f"#define AE_TEST_SCORE {float(s_ref[0]):.9g}f   // MSE vs the float window, host side\n")
        f.write(f"static const unsigned int ae_model_len = {len(blob)};\n")
        f.write(_carr("ae_model", np.frombuffer(blob, np.uint8), "uint8_t", 64))
        f.write(_carr("ae_test_in", q_in, "int8_t"))
        f.write(_carr("ae_test_out", q_out, "int8_t"))
    print(f"wrote {OUT_TFLITE}\nwrote {OUT_HEADER}  (tag {tag})")


if __name__ == "__main__":
    main()
