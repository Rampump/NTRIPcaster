#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_config.py - 把 config.ini.example 里「新增的配置项」补进现有的 config.ini

为什么需要这个脚本：
    升级时 update.sh / 容器 entrypoint 都不会覆盖 config.ini（那里的值是你的部署配置，
    必须原样保留）。但新版本新增的段和键在老配置文件里根本不存在 —— 于是会出现
    「代码里明明有 demo_mode / public_access / max_web_parsers，配置文件里一个都找不到」，
    用户既不知道有这些开关，也没法照着改。本脚本只补「缺的键」，已有值一个都不动。

行为：
    1. 以 config.ini.example 为准，逐段逐键检查 config.ini；
    2. 缺失的段整段追加到文件尾部，缺失的键追加到所属段的末尾（连同样例里的注释）；
    3. 已存在的键（哪怕值和示例不同）一律跳过，绝不覆盖；
    4. 默认 dry-run 只打印将补哪些项，加 --apply 才写文件（写前自动备份）；
    5. 幂等：重复执行不会产生重复键（configparser 遇到重复键会直接抛错）；
    6. 目标是软链接时（如 /opt/2rtk/config.ini -> /etc/2rtk/config.ini），
       写入落到链接终点，链接本身原样保留；悬空链接明确报错。

用法：
    python3 scripts/merge_config.py                       # 预览（默认 config.ini vs config.ini.example）
    python3 scripts/merge_config.py --apply               # 真写（自动备份）
    python3 scripts/merge_config.py --apply \\
        --config /etc/2rtk/config.ini --example /opt/2rtk/config.ini.example

退出码：
    0 = 正常（无论是否有新增项）
    2 = 配置文件无法解析（例如同一段里出现了重复键），不做任何修改
"""

import argparse
import configparser
import os
import shutil
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.environ.get('NTRIP_CONFIG_FILE') or os.path.join(_PROJECT_ROOT, 'config.ini')
DEFAULT_EXAMPLE = os.path.join(_PROJECT_ROOT, 'config.ini.example')


def read_text(path):
    """读文本，兼容 BOM。

    newline='' 是必须的：默认的通用换行模式会把 CRLF 悄悄转成 LF，
    写回时整份文件的行尾就变了 —— 提交到版本库会变成「全文件重写」的巨型 diff。
    """
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        return f.read()


def parse_sections(text):
    """把 ini 原文解析成 [(段名|None, 段头行下标, 段内行列表)]，用于原样改写。"""
    parts = []
    cur_name = None
    cur_lines = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            parts.append((cur_name, cur_lines))
            cur_name = stripped[1:-1].strip()
            cur_lines = [raw]
        else:
            cur_lines.append(raw)
    parts.append((cur_name, cur_lines))
    return parts


def example_blocks(example_text):
    """
    从示例文件里提取「每个段有哪些键」以及键前面的注释，保留书写顺序。

    返回：[(段名, [段前注释], [(键, 值原文, [键前注释])])]
    """
    sections = []
    pending_comments = []      # 尚未归属的注释行
    pending_key_comments = []  # 当前键上方连续的注释行

    cur = None
    for raw in example_text.splitlines():
        stripped = raw.strip()

        if stripped.startswith('[') and stripped.endswith(']'):
            cur = {
                'name': stripped[1:-1].strip(),
                'comments': list(pending_comments),
                'keys': [],
            }
            sections.append(cur)
            pending_comments = []
            pending_key_comments = []
            continue

        if cur is None:
            # 文件头部的说明注释
            if stripped:
                pending_comments.append(raw.rstrip())
            continue

        if not stripped:
            pending_key_comments = []
            continue

        if stripped.startswith('#') or stripped.startswith(';'):
            pending_key_comments.append(raw.rstrip())
            continue

        if '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            value = stripped.split('=', 1)[1].strip()
            cur['keys'].append((key, value, list(pending_key_comments)))
            pending_key_comments = []
            continue

        pending_key_comments = []

    return sections


def load_existing(path):
    """严格解析目标文件（重复键会抛错），返回 {段名: {键: 值}}。"""
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # 保留键名大小写
    parser.read(path, encoding='utf-8-sig')
    return {s: set(parser.options(s)) for s in parser.sections()}


def fmt_line(key, value):
    """还原成 ini 行；空值不留下尾部空格。"""
    return '%s = %s' % (key, value) if value else '%s =' % key


def build_inserts(existing, sections):
    """计算需要补的段与键。返回 (新段块, {段名: [待插入行]}, 统计)。"""
    new_sections = []
    key_inserts = {}
    added = []
    skipped = 0
    existing_sections = set(existing)

    for sec in sections:
        name = sec['name']
        if name not in existing_sections:
            block = []
            block.extend(sec['comments'])
            block.append('[%s]' % name)
            for key, value, comments in sec['keys']:
                block.extend(comments)
                block.append(fmt_line(key, value))
                added.append((name, key, value))
            new_sections.append(block)
            continue

        have = existing[name]
        missing_lines = []
        for key, value, comments in sec['keys']:
            if key in have:
                skipped += 1
                continue
            missing_lines.extend(comments)
            missing_lines.append(fmt_line(key, value))
            added.append((name, key, value))
        if missing_lines:
            key_inserts[name] = missing_lines

    return new_sections, key_inserts, added, skipped


def rebuild(text, sections, new_sections, key_inserts, newline):
    """在原文本基础上插入缺失的键与段，保留原有行、注释与空行。"""
    parts = parse_sections(text)
    out = []

    for name, lines in parts:
        if name is None:
            out.extend(lines)
            continue

        out.extend(lines)

        block = key_inserts.get(name)
        if block:
            # 插在段尾，但要放在段末尾空行之前，保持「空行分隔下一段」的观感
            trailing = 0
            while out and out[-1].strip() == '':
                out.pop()
                trailing += 1
            out.append('')
            out.extend(block)
            out.extend([''] * trailing if trailing else [''])

    if new_sections:
        if out and out[-1].strip() != '':
            out.append('')
        for block in new_sections:
            out.extend(block)
            out.append('')

    # 去掉文件末尾多余空行，统一以一个空行结尾
    while len(out) > 1 and out[-1].strip() == '' and out[-2].strip() == '':
        out.pop()
    if not out or out[-1].strip() != '':
        out.append('')

    return newline.join(out)


def resolve_target(path):
    """把可能存在的软链接解析成真实文件路径。

    生产上常见 /opt/2rtk/config.ini -> /etc/2rtk/config.ini 这种软链布局。
    写入必须落到链接终点，否则 os.replace 会把链接本身替换成普通文件：
    真文件一个字节没变（程序读的仍是旧配置），脚本却报「已写入」。

    悬空链接（指向的文件不存在）单独报错，而不是顺手新建一个把链接顶掉。
    """
    if not os.path.islink(path):
        return path
    target = os.path.realpath(path)
    if not os.path.exists(target):
        raise FileNotFoundError(
            '软链接 %s 指向的 %s 不存在（悬空链接），请先修复链接' % (path, target))
    return target


def write_in_place(text, target):
    """写入文本到 target（已解析软链接的真实路径）。

    直接截断重写而不是「写临时文件再 os.replace」：
    - 临时文件方案会把文件换掉，硬链接会断、inode 会变，软链接也会被顶掉；
    - 配置文件不是数据库，没有并发写事务需求，就地重写更安全。
    写失败时把原内容回滚回去，避免留下半截文件。
    """
    original = read_text(target)
    try:
        with open(target, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
    except OSError:
        # 回滚：尽量恢复原内容，别让配置文件变成半截
        try:
            with open(target, 'w', encoding='utf-8', newline='') as f:
                f.write(original)
        except OSError:
            pass
        raise
    # 保留原权限（/etc/2rtk/config.ini 可能是 640）
    try:
        shutil.copymode(target, target)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description='把 config.ini.example 的新增配置项补进 config.ini')
    ap.add_argument('--config', default=DEFAULT_CONFIG, help='目标配置文件（默认 config.ini）')
    ap.add_argument('--example', default=DEFAULT_EXAMPLE, help='示例配置（默认 config.ini.example）')
    ap.add_argument('--apply', action='store_true', help='真正写入；不加只预览')
    ap.add_argument('--no-backup', action='store_true', help='写入时不生成 .bak 备份（不推荐）')
    ap.add_argument('--quiet', action='store_true', help='只输出结果摘要')
    args = ap.parse_args()

    if not os.path.exists(args.config):
        print('目标配置不存在：%s' % args.config, file=sys.stderr)
        return 2
    if not os.path.exists(args.example):
        print('示例配置不存在：%s' % args.example, file=sys.stderr)
        return 2

    # 目标可能是软链接（/opt/2rtk/config.ini -> /etc/2rtk/config.ini），
    # 之后所有读写都走 resolve 出来的真实路径，链接本身原样保留
    try:
        target = resolve_target(args.config)
    except FileNotFoundError as exc:
        print('目标配置无法写入：%s' % exc, file=sys.stderr)
        return 2
    if target != args.config and not args.quiet:
        print('目标文件：%s -> %s（软链接，写入终点）' % (args.config, target))

    example_text = read_text(args.example)
    try:
        text = read_text(target)
        newline = '\r\n' if '\r\n' in text else '\n'
        existing = load_existing(target)
    except configparser.Error as exc:
        print('配置文件无法解析（可能有重复键），已放弃修改：%s' % exc, file=sys.stderr)
        print('提示：常见原因是同一段里出现了两个同名键（例如两行 demo_mode）。', file=sys.stderr)
        return 2

    sections = example_blocks(example_text)
    new_sections, key_inserts, added, skipped = build_inserts(existing, sections)

    if not added:
        if not args.quiet:
            print('配置已是最新：%s（%d 项均已存在）' % (args.config, skipped))
        else:
            print('unchanged')
        return 0

    if not args.quiet:
        print('目标文件：%s' % args.config)
        print('新增 %d 项（已有 %d 项保持不变）：' % (len(added), skipped))
        for name, key, value in added:
            shown = value if value else '(空)'
            print('  + [%s] %s = %s' % (name, key, shown))

    if not args.apply:
        if not args.quiet:
            print('\n这是预览。确认无误后加 --apply 写入（会自动备份）。')
        return 0

    merged = rebuild(text, sections, new_sections, key_inserts, newline)

    backup = None
    if not args.no_backup:
        backup = '%s.bak.%s' % (target, time.strftime('%Y%m%d-%H%M%S'))
        try:
            shutil.copy2(target, backup)
        except OSError as exc:
            print('备份失败，已放弃修改：%s' % exc, file=sys.stderr)
            return 2

    try:
        write_in_place(merged, target)
    except OSError as exc:
        print('写入失败：%s' % exc, file=sys.stderr)
        return 2

    # 写完立刻回读校验：能被 configparser 解析、且新增键确实在文件里
    try:
        after = load_existing(target)
    except configparser.Error as exc:
        print('写入后校验失败，正在还原备份：%s' % exc, file=sys.stderr)
        if backup:
            shutil.copy2(backup, target)
        return 2

    for name, key, _value in added:
        if key not in after.get(name, set()):
            print('写入后校验失败：[%s] %s 不在文件中，已还原备份' % (name, key), file=sys.stderr)
            if backup:
                shutil.copy2(backup, target)
            return 2

    if args.quiet:
        print('added %d' % len(added))
    else:
        print('\n已写入 %d 项。' % len(added))
        if backup:
            print('原文件已备份：%s' % backup)
        print('提示：程序只在启动时读配置，重启服务后生效。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
