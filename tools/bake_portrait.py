#!/usr/bin/env python3
"""Bake registered photo textures and a shallow 3D face mesh for the live portrait."""
import argparse
import json
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay

MOUTH = [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291, 375, 321, 405, 314, 17, 84, 181, 91, 146,
         78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95,
         200, 199, 176, 148, 152, 377, 400]
LEFT_EYE = [33, 246, 161, 160, 159, 158, 157, 173, 133, 155, 154, 153, 145, 144, 163, 7]
RIGHT_EYE = [362, 398, 384, 385, 386, 387, 388, 466, 263, 249, 390, 373, 374, 380, 381, 382]
ALIGN = [1, 4, 6, 10, 33, 133, 168, 197, 234, 263, 362, 454]
FRAME_COUNT = 9
COLS, ROWS = 64, 96


def read_plate(path, landmarker):
    rgba = Image.open(path).convert('RGBA')
    rgb = np.asarray(rgba.convert('RGB'))
    tracked = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if len(tracked.face_landmarks) != 1:
        raise ValueError(f'{path}: expected one unobstructed frontal face')
    w, h = rgba.size
    points = np.array([(p.x * w, p.y * h, p.z * w) for p in tracked.face_landmarks[0]])
    return rgba, rgb, points


def register(rgb, points, base, size, indices):
    """A similarity fit cancels incidental position/scale differences between the poses."""
    anchor = points[indices, :2].astype(np.float32)
    target = base[indices, :2].astype(np.float32)
    matrix, _ = cv2.estimateAffinePartial2D(anchor, target, method=cv2.LMEDS)
    if matrix is None:
        raise ValueError('could not register the expression plate')
    xy = cv2.transform(points[:, :2][None, ...], matrix)[0]
    aligned = cv2.warpAffine(rgb, matrix, size, flags=cv2.INTER_CUBIC)
    return aligned, xy


def control_points(base, target, feature, size, margins, box=None):
    core = base[feature, :2]
    pose = target[feature]
    if box is None:
        width = np.ptp(core[:, 0])
        low = np.minimum(core.min(axis=0), pose.min(axis=0)) - width * np.array(margins[:2])
        high = np.maximum(core.max(axis=0), pose.max(axis=0)) + width * np.array(margins[2:])
        x0, y0 = np.maximum(0, np.floor(low)).astype(int).tolist()
        x1, y1 = np.minimum(size, np.ceil(high)).astype(int).tolist()
    else:
        x0, y0, x1, y1 = box
    xs, ys = np.linspace(x0, x1 - 1, 7), np.linspace(y0, y1 - 1, 7)
    border = np.concatenate([
        np.c_[xs, np.full_like(xs, y0)], np.c_[xs, np.full_like(xs, y1 - 1)],
        np.c_[np.full(5, x0), ys[1:-1]], np.c_[np.full(5, x1 - 1), ys[1:-1]],
    ])
    return np.vstack([core, border]), np.vstack([pose, border]), (x0, y0, x1, y1)


def remap_pose(image, coordinates, vertex, barycentric, shape):
    sample = (coordinates[vertex] * barycentric[..., None]).sum(axis=1).reshape(*shape, 2)
    return cv2.remap(image, sample.astype(np.float32), None, cv2.INTER_CUBIC,
                     borderMode=cv2.BORDER_REPLICATE)


def morph_frames(original, edit, base, target, feature, size, margins, is_mouth, box=None):
    src, dst, (x0, y0, x1, y1) = control_points(base, target, feature, size, margins, box)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    pixels = np.stack([xx.ravel(), yy.ravel()], axis=-1)
    neutral = original[y0:y1, x0:x1]
    distance = np.minimum.reduce([xx - x0, x1 - 1 - xx, yy - y0, y1 - 1 - yy])
    feather = np.clip(distance / (min(xx.shape) * 0.14), 0, 1)[..., None]
    edge = (distance >= 3) & (distance < min(xx.shape) * 0.12)
    correction = np.clip(np.median((neutral.astype(float) - edit[y0:y1, x0:x1])[edge], axis=0), -22, 22)

    def frame(amount):
        mesh = Delaunay(src + (dst - src) * amount)
        index = mesh.find_simplex(pixels).clip(0)
        transform = mesh.transform[index]
        bc = np.einsum('ijk,ik->ij', transform[:, :2], pixels - transform[:, 2])
        bc = np.column_stack([bc, 1 - bc.sum(axis=1)]).clip(0, 1)
        bc /= bc.sum(axis=1, keepdims=True)
        before = remap_pose(original, src, mesh.simplices[index], bc, xx.shape)
        after = remap_pose(edit, dst, mesh.simplices[index], bc, xx.shape) + correction
        # The open-mouth plate supplies teeth and the interior as soon as a gap forms.
        mix = amount ** (0.56 if is_mouth else 0.82)
        interpolated = before * (1 - mix) + after * mix
        return np.uint8(np.clip(neutral + feather * (interpolated - neutral), 0, 255))

    return [neutral, *[frame(i / (FRAME_COUNT - 1)) for i in range(1, FRAME_COUNT)]], [x0, y0, x1-x0, y1-y0]


def bake_atlas(base_image, base, mouths, blink, size):
    blink_rgb, blink_points = blink
    margins = (.43, .28, .43, .45)
    bounds = [control_points(base, pts, MOUTH, size, margins)[2] for _, pts in mouths.values()]
    box = [min(p[0] for p in bounds), min(p[1] for p in bounds),
           max(p[2] for p in bounds), max(p[3] for p in bounds)]
    bands = {name: morph_frames(base_image, rgb, base, pts, MOUTH, size, margins, True, box)
             for name, (rgb, pts) in mouths.items()}
    for name, feature in [('left', LEFT_EYE), ('right', RIGHT_EYE)]:
        bands[name] = morph_frames(base_image, blink_rgb, base, blink_points,
                                   feature, size, (.29, .38, .29, .38), False)
    pad = 2
    atlas = Image.new('RGB', (max((box[2] + pad * 2) * FRAME_COUNT for _, box in bands.values()),
                              sum(box[3] + pad * 2 for _, box in bands.values())))
    regions = {}
    y = 0
    for name, (frames, box) in bands.items():
        stride = box[2] + pad * 2
        for i, frame in enumerate(frames):
            tile = np.pad(frame, ((pad, pad), (pad, pad), (0, 0)), mode='edge')
            atlas.paste(Image.fromarray(tile), (i * stride, y))
        regions[name] = {'rect': box, 'start': [pad, y + pad], 'stride': stride}
        y += box[3] + pad * 2
    return atlas, regions


def bake_depth(points, size):
    """Keep camera-facing depth in physical proportions, with facial relief from tracked z."""
    w, h = size
    v, u = np.mgrid[0:1:complex(0, ROWS + 1), 0:1:complex(0, COLS + 1)]
    q = np.stack([u * w, v * h], axis=-1)
    face = points[:468]
    x = (u - points[1, 0] / w) / 0.34
    y = (v - points[9, 1] / h) / 0.25
    scalp = 0.079 * np.exp(-1.5 * (x*x + y*y)) - .025
    chest = .063 * np.maximum(0, 1-((u-.5)/.53)**2) * np.clip((v-.52)/.17, 0, 1)
    z = np.clip(-(face[:, 2] - np.median(face[:, 2])) * 1.6 / h, -.032, .106) + .068
    relief = LinearNDInterpolator(face[:, :2], z)(q)
    # Blur only the edge of the facial depth field so cheeks blend into the hair and neck.
    inside = np.isfinite(relief).astype(np.float32)
    soft = cv2.GaussianBlur(inside, (7, 7), 1.1) * inside
    depth = (scalp + chest) * (1-soft) + np.nan_to_num(relief) * soft
    return np.rint(depth.ravel() * 10000).astype(int).tolist()


def make_assets(args, landmarker):
    alpha, base_rgb, base = read_plate(args.neutral, landmarker)
    if alpha.getchannel('A').getextrema()[0] > 10:
        raise ValueError('neutral plate must have a transparent background')
    _, blink_rgb, blink = read_plate(args.blink, landmarker)
    size = alpha.size
    mouths = {}
    for name, path in [('mouth', args.mouth), ('round', args.round), ('wide', args.wide), ('smile', args.smile)]:
        if path:
            _, rgb, points = read_plate(path, landmarker)
            mouths[name] = register(rgb, points, base, size, ALIGN)
    blink = register(blink_rgb, blink, base, size, ALIGN + [61, 291])
    atlas, regions = bake_atlas(base_rgb, base, mouths, blink, size)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    # Snap the almost-opaque interior to alpha 255 while retaining fine hair edges.
    alpha.putalpha(alpha.getchannel('A').point(lambda a: 0 if a < 3 else min(255, round(a * 255 / 250))))
    alpha.save(output / 'neutral.webp', quality=92, method=6)
    atlas.save(output / 'expressions.webp', quality=94, method=6)
    doc = {
        'version': 1, 'size': size, 'atlasSize': atlas.size, 'frames': FRAME_COUNT,
        'regions': regions, 'grid': [COLS, ROWS], 'depth': bake_depth(base, size),
        'neck': round(float(base[152, 1] / size[1]) + .08, 5),
    }
    (output / 'rig.json').write_text(json.dumps(doc, separators=(',', ':')) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('neutral', 'mouth', 'blink', 'task', 'output'):
        parser.add_argument(argument, type=Path)
    parser.add_argument('--round', type=Path)
    parser.add_argument('--wide', type=Path)
    parser.add_argument('--smile', type=Path)
    args = parser.parse_args()
    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.task)), num_faces=1,
    )
    with mp.tasks.vision.FaceLandmarker.create_from_options(options) as landmarker:
        make_assets(args, landmarker)


if __name__ == '__main__':
    main()
