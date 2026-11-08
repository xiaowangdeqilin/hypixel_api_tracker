//! 逐行文本 diff：只返回发生变化的片段（带少量上下文）。
//!
//! 原 pack_inspector.diff_text_lines 的移植，用 LCS 代替 difflib.SequenceMatcher，
//! 输出结构一致：
//!
//! ```json
//! [{"old_start": 1, "new_start": 1, "lines": [{"kind": "ctx|del|add", "text": "..."}]}]
//! ```

const DIFF_CONTEXT_LINES: usize = 2;
const DIFF_MAX_HUNKS: usize = 8;
const DIFF_MAX_LINES_PER_HUNK: usize = 40;
/// LCS 矩阵规模上限，超出则降级为整段替换（避免内存爆炸）。
const LCS_MAX_CELLS: usize = 4_000_000;

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct DiffLine {
    pub kind: String, // ctx | del | add
    pub text: String,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Hunk {
    pub old_start: usize,
    pub new_start: usize,
    pub lines: Vec<DiffLine>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub truncated: Option<usize>,
}

fn ctx(text: &str) -> DiffLine {
    DiffLine { kind: "ctx".into(), text: text.into() }
}
fn del(text: &str) -> DiffLine {
    DiffLine { kind: "del".into(), text: text.into() }
}
fn add(text: &str) -> DiffLine {
    DiffLine { kind: "add".into(), text: text.into() }
}

/// 计算两个行序列的 LCS 操作序列（equal/replace/delete/insert 的 opcodes）。
fn lcs_opcodes(a: &[&str], b: &[&str]) -> Vec<(char, usize, usize, usize, usize)> {
    let n = a.len();
    let m = b.len();
    if n * m > LCS_MAX_CELLS {
        // 降级：整段 replace
        return if n == 0 {
            vec![('i', 0, 0, 0, m)]
        } else if m == 0 {
            vec![('d', 0, n, 0, 0)]
        } else {
            vec![('r', 0, n, 0, m)]
        };
    }
    // dp[i][j] = a[i..] 与 b[j..] 的 LCS 长度
    let width = m + 1;
    let mut dp = vec![0u32; (n + 1) * width];
    for i in (0..n).rev() {
        for j in (0..m).rev() {
            dp[i * width + j] = if a[i] == b[j] {
                dp[(i + 1) * width + j + 1] + 1
            } else {
                dp[(i + 1) * width + j].max(dp[i * width + j + 1])
            };
        }
    }
    // 回溯生成 opcode 流（合并相邻同类操作）
    let mut ops: Vec<(char, usize, usize, usize, usize)> = Vec::new();
    let push_op = |tag: char, i1: usize, i2: usize, j1: usize, j2: usize, ops: &mut Vec<_>| {
        if let Some(last) = ops.last_mut() {
            let (lt, li1, li2, lj1, lj2) = *last;
            if lt == tag && li2 == i1 && lj2 == j1 {
                *last = (lt, li1, i2, lj1, j2);
                return;
            }
        }
        ops.push((tag, i1, i2, j1, j2));
    };
    let (mut i, mut j) = (0usize, 0usize);
    while i < n && j < m {
        if a[i] == b[j] {
            let (i1, j1) = (i, j);
            while i < n && j < m && a[i] == b[j] {
                i += 1;
                j += 1;
            }
            push_op('e', i1, i, j1, j, &mut ops);
        } else if dp[(i + 1) * width + j] >= dp[i * width + j + 1] {
            push_op('d', i, i + 1, j, j, &mut ops);
            i += 1;
        } else {
            push_op('i', i, i, j, j + 1, &mut ops);
            j += 1;
        }
    }
    if i < n {
        push_op('d', i, n, j, j, &mut ops);
    }
    if j < m {
        push_op('i', i, i, j, m, &mut ops);
    }
    ops
}

/// 对两个文本做逐行比对，只返回发生变化的片段。
pub fn diff_text_lines(old_text: &str, new_text: &str) -> Vec<Hunk> {
    if old_text == new_text {
        return Vec::new();
    }
    let old_lines: Vec<&str> = old_text.lines().collect();
    let new_lines: Vec<&str> = new_text.lines().collect();
    let ops = lcs_opcodes(&old_lines, &new_lines);

    let mut hunks: Vec<Hunk> = Vec::new();
    let mut pending: Vec<DiffLine> = Vec::new();
    let mut old_start = 0usize;
    let mut new_start = 0usize;
    let mut last_equal_end: Option<(usize, usize)> = None; // (i2, j2) 最近一段 equal 的结束位置

    let flush = |pending: &mut Vec<DiffLine>,
                     old_start: usize,
                     new_start: usize,
                     hunks: &mut Vec<Hunk>| {
        if pending.is_empty() {
            return;
        }
        let truncated = (pending.len() > DIFF_MAX_LINES_PER_HUNK)
            .then(|| pending.len() - DIFF_MAX_LINES_PER_HUNK);
        let lines: Vec<DiffLine> = pending.drain(..).take(DIFF_MAX_LINES_PER_HUNK).collect();
        // keep 顺序：drain 已 take，需先截断再记录 truncated —— 上面 take 后
        // pending 剩余部分丢弃；truncated 已按 pending.len() 计算，正确。
        hunks.push(Hunk {
            old_start: old_start + 1,
            new_start: new_start + 1,
            lines,
            truncated,
        });
    };

    for (tag, i1, i2, j1, j2) in ops {
        if tag == 'e' {
            if i2 - i1 <= 2 * DIFF_CONTEXT_LINES && !pending.is_empty() {
                // 变化段之间的少量上下文保留，让人看得懂位置
                for ln in &old_lines[i1..i2] {
                    pending.push(ctx(ln));
                }
            } else if !pending.is_empty() {
                for ln in &old_lines[i1..(i1 + DIFF_CONTEXT_LINES).min(i2)] {
                    pending.push(ctx(ln));
                }
                flush(&mut pending, old_start, new_start, &mut hunks);
            }
            last_equal_end = Some((i2, j2));
            continue;
        }
        if pending.is_empty() {
            let from_old = i1.saturating_sub(DIFF_CONTEXT_LINES);
            let from_new = j1.saturating_sub(DIFF_CONTEXT_LINES);
            old_start = from_old;
            new_start = from_new;
            let ctx_lines: Vec<&str> = if from_new < j1 && !new_lines[from_new..j1].is_empty() {
                new_lines[from_new..j1].to_vec()
            } else if from_old < i1 {
                old_lines[from_old..i1].to_vec()
            } else {
                Vec::new()
            };
            for ln in ctx_lines {
                pending.push(ctx(ln));
            }
        }
        if tag == 'r' || tag == 'd' {
            for ln in &old_lines[i1..i2] {
                pending.push(del(ln));
            }
        }
        if tag == 'r' || tag == 'i' {
            for ln in &new_lines[j1..j2] {
                pending.push(add(ln));
            }
        }
        if pending.len() > DIFF_MAX_LINES_PER_HUNK {
            flush(&mut pending, old_start, new_start, &mut hunks);
            // flush 后继续同一 opcode 的剩余部分（新 hunk 的起点未知，
            // 使用当前位置作为近似起点）
            old_start = i1;
            new_start = j1;
        }
    }

    if !pending.is_empty() {
        if let Some((i2, _j2)) = last_equal_end {
            for ln in &old_lines[i2..(i2 + DIFF_CONTEXT_LINES).min(old_lines.len())] {
                pending.push(ctx(ln));
            }
        }
        flush(&mut pending, old_start, new_start, &mut hunks);
    }

    hunks.truncate(DIFF_MAX_HUNKS);
    hunks
}
