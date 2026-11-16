"""大まんまる（2×2の合体スライム）と石まんまるを Blender でレンダリングする。

    # Blender 本体で
    blender -b -P tools/blender/render_extras.py
    # もしくは pip の bpy モジュールで
    python tools/blender/render_extras.py [--calib]

出力（いずれも透明背景の PNG）:
    sprites/big_0.png .. big_3.png  大まんまる（行=アトラスの色番号）。顔はゲーム側で描く
    sprites/stone.png               石まんまる（1マス）

座標系: 1マス = 1.0。x が右、z が上。カメラは -Y 側から正面を写す正射影。
既存タイル（tiles/atlas.png）と同じく、描画範囲は上下左右に 0.15 マスはみ出した
正方形（1マスなら 1.3、2×2 なら 2.3 マス四方）。
"""
import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "sprites")
PAD = 0.15
LIGHT = float(os.environ.get("MM_LIGHT", "9"))
WORLD = float(os.environ.get("MM_WORLD", "0.25"))
EMIT = float(os.environ.get("MM_EMIT", "0.0"))
COAT = float(os.environ.get("MM_COAT", "0.25"))

# アトラスの色番号順（0=みどり 1=ピンク 2=みずいろ 3=きいろ）
def srgb(h):
    """'#rrggbb' → リニアRGB"""
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return tuple(f(int(h[i:i + 2], 16)) for i in (1, 3, 5))


SLIME_COLORS = [srgb(h) for h in ("#78ffcd", "#ffb4ec", "#8cdcff", "#fffa78")]


# 顔の大きさ（1マスのスライムに対する倍率）。index.html の BIG_FACE と合わせる
FACE_SCALE = {1: 1.0, 2: 1.7}


# ---------------------------------------------------------------- scene
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 96
    sc.cycles.use_denoising = True
    try:
        sc.cycles.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.image_settings.compression = 100
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0.0
    world = bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (1.0, 0.97, 0.98, 1.0)
    bg.inputs["Strength"].default_value = WORLD
    return sc


def add_camera(sc, cx, cz, size, res):
    cam = bpy.data.cameras.new("cam")
    cam.type = "ORTHO"
    cam.ortho_scale = size
    ob = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(ob)
    ob.location = (cx, -20.0, cz)
    ob.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = ob
    sc.render.resolution_x = res
    sc.render.resolution_y = res
    sc.render.resolution_percentage = 100


def add_area(sc, name, loc, target, energy, size, color=(1, 1, 1)):
    l = bpy.data.lights.new(name, "AREA")
    l.energy = energy
    l.size = size
    l.color = color
    ob = bpy.data.objects.new(name, l)
    sc.collection.objects.link(ob)
    ob.location = loc
    d = Vector(target) - Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


def add_lights(sc, cx, cz, s):
    """s = スライムの大きさの目安（1マスなら1）"""
    e = LIGHT * s * s
    add_area(sc, "key", (cx - 2.0 * s, -3.0 * s, cz + 3.0 * s), (cx, 0, cz), 26 * e, 2.5 * s)
    add_area(sc, "fill", (cx + 3.0 * s, -2.5 * s, cz + 0.5 * s), (cx, 0, cz), 9 * e, 3.0 * s, (1.0, 0.95, 1.0))
    add_area(sc, "rim", (cx + 0.5 * s, 3.0 * s, cz + 2.5 * s), (cx, 0, cz), 5 * e, 3.0 * s)
    add_area(sc, "bounce", (cx, -2.5 * s, cz - 2.5 * s), (cx, 0, cz), 4 * e, 4.0 * s)


# ---------------------------------------------------------------- shapes
def ellipsoid(name, center, radii, segs=96):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segs, ring_count=segs // 2, radius=1.0, location=center)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = radii
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.object.shade_smooth()
    return ob


def front_y(center, radii, x, z):
    """楕円体の正面（-Y側）の表面の y 座標"""
    cx, cy, cz = center
    a, b, c = radii
    t = 1.0 - ((x - cx) / a) ** 2 - ((z - cz) / c) ** 2
    return cy - b * math.sqrt(max(0.0, t))


def surface_curve(name, pts, center, radii, depth, sink, mat):
    """楕円体の正面に貼りつく太さ depth の線（目・口のほり込み）"""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = depth
    cu.bevel_resolution = 6
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for i, (x, z) in enumerate(pts):
        y = front_y(center, radii, x, z) + sink
        sp.points[i].co = (x, y, z, 1.0)
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    ob.data.materials.append(mat)
    return ob


def arc_pts(cx, cz, w, h, n=16, flip=False):
    """幅 w・高さ h のゆるい弧（flip=False で下に凸 ＝「︶」）"""
    pts = []
    for i in range(n + 1):
        u = i / n
        x = cx - w / 2 + w * u
        z = cz - h * math.sin(math.pi * u) * (-1 if flip else 1)
        pts.append((x, z))
    return pts


# ---------------------------------------------------------------- materials
def slime_material(rgb):
    m = bpy.data.materials.new("slime")
    m.use_nodes = True
    nt = m.node_tree
    n = nt.nodes
    l = nt.links
    bsdf = n["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.32
    bsdf.inputs["Coat Weight"].default_value = COAT
    bsdf.inputs["Coat Roughness"].default_value = 0.18
    bsdf.inputs["Subsurface Weight"].default_value = 0.15
    bsdf.inputs["Subsurface Radius"].default_value = (1.0, 0.7, 0.7)
    bsdf.inputs["Subsurface Scale"].default_value = 0.05
    bsdf.inputs["Emission Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Emission Strength"].default_value = EMIT
    # ふちをうっすら灰色にする（タイルの輪郭線と合わせる）
    lw = n.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.08
    edge = n.new("ShaderNodeBsdfDiffuse")
    edge.inputs["Color"].default_value = (0.55, 0.5, 0.6, 1.0)
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.80
    ramp.color_ramp.elements[1].position = 0.97
    mix = n.new("ShaderNodeMixShader")
    out = n["Material Output"]
    l.new(lw.outputs["Facing"], ramp.inputs["Fac"])
    l.new(ramp.outputs["Color"], mix.inputs["Fac"])
    l.new(bsdf.outputs["BSDF"], mix.inputs[1])
    l.new(edge.outputs["BSDF"], mix.inputs[2])
    l.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


def stone_material():
    m = bpy.data.materials.new("stone")
    m.use_nodes = True
    nt = m.node_tree
    n = nt.nodes
    l = nt.links
    bsdf = n["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.85
    tc = n.new("ShaderNodeTexCoord")
    # まだら模様
    noise = n.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 9.0
    noise.inputs["Detail"].default_value = 6.0
    noise.inputs["Roughness"].default_value = 0.6
    l.new(tc.outputs["Object"], noise.inputs["Vector"])
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[0].color = (0.25, 0.24, 0.26, 1)
    ramp.color_ramp.elements[1].position = 0.70
    ramp.color_ramp.elements[1].color = (0.50, 0.48, 0.47, 1)
    l.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    # 細かい粒
    speck = n.new("ShaderNodeTexNoise")
    speck.inputs["Scale"].default_value = 60.0
    speck.inputs["Detail"].default_value = 2.0
    l.new(tc.outputs["Object"], speck.inputs["Vector"])
    sramp = n.new("ShaderNodeValToRGB")
    sramp.color_ramp.elements[0].position = 0.62
    sramp.color_ramp.elements[0].color = (1, 1, 1, 1)
    sramp.color_ramp.elements[1].position = 0.70
    sramp.color_ramp.elements[1].color = (0.55, 0.53, 0.55, 1)
    l.new(speck.outputs["Fac"], sramp.inputs["Fac"])
    mul = n.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    mul.inputs["Factor"].default_value = 1.0
    l.new(ramp.outputs["Color"], mul.inputs[6])
    l.new(sramp.outputs["Color"], mul.inputs[7])
    # ひび割れ（ボロノイの境界を、別のノイズで一部だけ残す）
    vor = n.new("ShaderNodeTexVoronoi")
    vor.feature = "DISTANCE_TO_EDGE"
    vor.inputs["Scale"].default_value = 2.2
    l.new(tc.outputs["Object"], vor.inputs["Vector"])
    edge = n.new("ShaderNodeMapRange")
    edge.inputs["From Min"].default_value = 0.0
    edge.inputs["From Max"].default_value = 0.025
    edge.inputs["To Min"].default_value = 1.0
    edge.inputs["To Max"].default_value = 0.0
    l.new(vor.outputs["Distance"], edge.inputs["Value"])
    mnoise = n.new("ShaderNodeTexNoise")
    mnoise.inputs["Scale"].default_value = 2.5
    mnoise.inputs["Detail"].default_value = 1.0
    l.new(tc.outputs["Object"], mnoise.inputs["Vector"])
    mrange = n.new("ShaderNodeMapRange")
    mrange.inputs["From Min"].default_value = 0.48
    mrange.inputs["From Max"].default_value = 0.56
    l.new(mnoise.outputs["Fac"], mrange.inputs["Value"])
    crack = n.new("ShaderNodeMath")
    crack.operation = "MULTIPLY"
    l.new(edge.outputs["Result"], crack.inputs[0])
    l.new(mrange.outputs["Result"], crack.inputs[1])
    cramp = n.new("ShaderNodeValToRGB")
    cramp.color_ramp.elements[0].position = 0.0
    cramp.color_ramp.elements[0].color = (1, 1, 1, 1)
    cramp.color_ramp.elements[1].position = 1.0
    cramp.color_ramp.elements[1].color = (0.12, 0.11, 0.12, 1)
    l.new(crack.outputs["Value"], cramp.inputs["Fac"])
    mul2 = n.new("ShaderNodeMix")
    mul2.data_type = "RGBA"
    mul2.blend_type = "MULTIPLY"
    mul2.inputs["Factor"].default_value = 1.0
    l.new(mul.outputs[2], mul2.inputs[6])
    l.new(cramp.outputs["Color"], mul2.inputs[7])
    l.new(mul2.outputs[2], bsdf.inputs["Base Color"])
    # でこぼこ
    bump = n.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.02
    add = n.new("ShaderNodeMath")
    add.operation = "MULTIPLY"
    inv = n.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    l.new(crack.outputs["Value"], inv.inputs[1])
    l.new(noise.outputs["Fac"], add.inputs[0])
    l.new(inv.outputs["Value"], add.inputs[1])
    l.new(add.outputs["Value"], bump.inputs["Height"])
    l.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return m


def flat_material(rgb, rough=0.4):
    m = bpy.data.materials.new("flat")
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1.0)
    b.inputs["Roughness"].default_value = rough
    return m


def rockify(ob, strength, scale):
    tex = bpy.data.textures.new("rock", "CLOUDS")
    tex.noise_scale = scale
    tex.noise_depth = 2
    mod = ob.modifiers.new("disp", "DISPLACE")
    mod.texture = tex
    mod.strength = strength
    mod.mid_level = 0.5
    mod.texture_coords = "OBJECT"


# ---------------------------------------------------------------- builds
def render(sc, path):
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print("wrote", path)


def build_slime(cells, rgb):
    """cells=1 なら1マス、2 なら 2×2 の大まんまる（顔なし）"""
    sc = reset_scene()
    span = float(cells)
    cx = span / 2
    if cells == 1:
        radii = (0.472, 0.40, 0.365)
    else:
        radii = (0.97, 0.85, 0.89)
    center = (cx, 0.0, 0.033 + radii[2])
    body = ellipsoid("body", center, radii)
    body.data.materials.append(slime_material(rgb))
    # ほっぺ（タイルと同じ小さなピンクのふくらみ）
    cheek = flat_material((1.0, 0.55, 0.65), 0.35)
    k = FACE_SCALE[cells]
    for sx in (-1, 1):
        x = cx + sx * 0.33 * k
        z = center[2] - 0.02 * k
        y = front_y(center, radii, x, z)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0, location=(x, y + 0.014 * k, z))
        c = bpy.context.active_object
        ck = k * (1.0 if cells == 1 else 1.3)
        c.scale = (0.05 * ck, 0.026 * ck, 0.036 * ck)
        bpy.ops.object.shade_smooth()
        c.data.materials.append(cheek)
    size = span + 2 * PAD
    add_camera(sc, cx, span / 2, size, 128 if cells == 1 else 320)
    add_lights(sc, cx, center[2], span)
    return sc


def build_stone():
    """1マスの石まんまる（大まんまるは石にならない）"""
    sc = reset_scene()
    span = 1.0
    cx = span / 2
    radii = (0.47, 0.40, 0.36)
    center = (cx, 0.0, 0.03 + radii[2])
    body = ellipsoid("stone", center, radii, segs=128)
    rockify(body, 0.045 * span, 0.25 * span)
    body.data.materials.append(stone_material())
    k = span
    groove = flat_material((0.10, 0.09, 0.11), 0.7)
    # 固まった顔: 閉じた目「︶ ︶」と、ぶすっとした口「へ」
    ez = center[2] + 0.06 * k
    for sx in (-1, 1):
        ex = cx + sx * 0.185 * k
        pts = arc_pts(ex, ez, 0.13 * k, 0.03 * k)
        surface_curve("eye", pts, center, radii, 0.016 * k, -0.004, groove)
    mz = center[2] - 0.08 * k
    mouth = [(cx - 0.06 * k, mz - 0.018 * k), (cx, mz + 0.012 * k), (cx + 0.06 * k, mz - 0.018 * k)]
    surface_curve("mouth", mouth, center, radii, 0.013 * k, -0.004, groove)
    size = span + 2 * PAD
    add_camera(sc, cx, span / 2, size, 160)
    add_lights(sc, cx, center[2], span)
    return sc


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    os.makedirs(OUT, exist_ok=True)
    if "--calib" in argv:
        # 既存タイルと見比べる用（1マスの顔なしスライム）
        for i, rgb in enumerate(SLIME_COLORS):
            sc = build_slime(1, rgb)
            render(sc, os.path.join(argv[argv.index("--calib") + 1], "calib_%d.png" % i))
        return
    for i, rgb in enumerate(SLIME_COLORS):
        sc = build_slime(2, rgb)
        render(sc, os.path.join(OUT, "big_%d.png" % i))
    render(build_stone(), os.path.join(OUT, "stone.png"))


if __name__ == "__main__":
    main()
