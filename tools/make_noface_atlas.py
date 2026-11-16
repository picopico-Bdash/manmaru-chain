"""tiles/atlas.png から目（とまわりのハイライト）を消した tiles/atlas_noface.png を作る。

ごきげん度で表情を変えるため、目はゲーム側（canvas）で描き直す。
アトラスの目は全タイル共通の位置にあるので、その周りだけをマスクして
ラプラス補間（周囲の色からなめらかに埋める）で塗りつぶす。

    python3 tools/make_noface_atlas.py
"""
import os
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tiles", "atlas.png")
DST = os.path.join(ROOT, "tiles", "atlas_noface.png")

ATS, AG, AT = 132, 2, 128          # タイル間隔 / 余白 / タイルサイズ（index.html と同じ）
EYES = [(45.5, 66.5), (81.5, 66.5)]  # タイル内の目の中心（px）


def eye_mask(tile):
    h, w = tile.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = tile[..., :3].astype(float)
    lum = rgb.sum(-1)
    m = np.zeros((h, w), bool)
    for ex, ey in EYES:
        # 目そのもの（楕円）
        m |= ((xx - ex) / 6.5) ** 2 + ((yy - ey) / 10.0) ** 2 <= 1.0
        # 目のハイライト（目のすぐ近くの白っぽい画素）
        near = ((xx - ex) / 11.0) ** 2 + ((yy - ey) / 13.0) ** 2 <= 1.0
        sat = rgb.max(-1) - rgb.min(-1)
        m |= near & (lum > 690) & (sat < 60)
        # 暗い画素（目のふち）
        m |= near & (lum < 420)
    # 1px 太らせて縁のにじみも消す
    d = m.copy()
    d[1:, :] |= m[:-1, :]; d[:-1, :] |= m[1:, :]
    d[:, 1:] |= m[:, :-1]; d[:, :-1] |= m[:, 1:]
    return d


def laplace_fill(tile, mask, iters=1500):
    out = tile.astype(float).copy()
    # 初期値: マスク外の平均
    for ch in range(3):
        out[..., ch][mask] = out[..., ch][~mask].mean()
    ys, xs = np.where(mask)
    for _ in range(iters):
        up = out[np.clip(ys - 1, 0, None), xs, :3]
        dn = out[np.clip(ys + 1, None, out.shape[0] - 1), xs, :3]
        lf = out[ys, np.clip(xs - 1, 0, None), :3]
        rt = out[ys, np.clip(xs + 1, None, out.shape[1] - 1), :3]
        out[ys, xs, :3] = (up + dn + lf + rt) / 4.0
    return out


def main():
    atlas = np.array(Image.open(SRC).convert("RGBA"))
    res = atlas.copy()
    rows = atlas.shape[0] // ATS
    cols = atlas.shape[1] // ATS
    for r in range(rows):
        for c in range(cols):
            y0, x0 = r * ATS + AG, c * ATS + AG
            tile = atlas[y0:y0 + AT, x0:x0 + AT]
            mask = eye_mask(tile) & (tile[..., 3] > 0)
            filled = laplace_fill(tile, mask)
            res[y0:y0 + AT, x0:x0 + AT, :3] = np.clip(np.round(filled[..., :3]), 0, 255).astype(np.uint8)
    Image.fromarray(res).save(DST, optimize=True)
    print("wrote", DST)


if __name__ == "__main__":
    main()
