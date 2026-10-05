//! The running executable as patternsleuth reads it, on Linux.
//!
//! patternsleuth's `process::internal::read_image` parses the ELF straight from the mapped memory: a
//! slice from the first PT_LOAD to the end of the last one, indexed as if it were the file. That is
//! right for everything the loader maps, and wrong for what it does not: the section header table and
//! the non-ALLOC sections (.shstrtab, .symtab, .comment) sit at file offsets past the loaded code,
//! and those offsets land inside the memory of the LAST segment - usually .bss. While that stretch of
//! .bss is still zero the parse goes through (Smalland, Palworld); once the game has written there,
//! the section headers are garbage and the scan fails with "Unsupported file format" on every attempt,
//! millions of times, until UE4SS gives up (Astro Colony).
//!
//! Here the bytes the parser needs and the loader never mapped come from /proc/self/exe. Memory is
//! used as is when it already matches the file; otherwise a copy is patched once and kept for every
//! later scan attempt.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::sync::OnceLock;

const PT_LOAD: u32 = 1;
const SHF_ALLOC: u64 = 0x2;
const SHT_NOBITS: u32 = 8;

struct Header {
    phoff: u64,
    shoff: u64,
    phentsize: u16,
    phnum: u16,
    shentsize: u16,
    shnum: u16,
}

fn u16_at(b: &[u8], o: usize) -> u16 {
    u16::from_le_bytes([b[o], b[o + 1]])
}
fn u32_at(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes(b[o..o + 4].try_into().unwrap())
}
fn u64_at(b: &[u8], o: usize) -> u64 {
    u64::from_le_bytes(b[o..o + 8].try_into().unwrap())
}

fn read_at(file: &mut File, offset: u64, len: usize) -> std::io::Result<Vec<u8>> {
    let mut buf = vec![0u8; len];
    file.seek(SeekFrom::Start(offset))?;
    file.read_exact(&mut buf)?;
    Ok(buf)
}

fn header(file: &mut File) -> Result<Header, Box<dyn std::error::Error>> {
    let h = read_at(file, 0, 64)?;
    if &h[0..4] != b"\x7fELF" || h[4] != 2 {
        return Err("the executable is not a 64-bit ELF".into());
    }
    Ok(Header {
        phoff: u64_at(&h, 0x20),
        shoff: u64_at(&h, 0x28),
        phentsize: u16_at(&h, 0x36),
        phnum: u16_at(&h, 0x38),
        shentsize: u16_at(&h, 0x3A),
        shnum: u16_at(&h, 0x3C),
    })
}

/// Load address of the first mapping of the executable (offset 0), from /proc/self/maps.
fn mapping_start(exe: &str) -> Result<u64, Box<dyn std::error::Error>> {
    let maps = std::fs::read_to_string("/proc/self/maps")?;
    for line in maps.lines() {
        let mut parts = line.split_whitespace();
        let (range, _perms, offset) = (parts.next(), parts.next(), parts.next());
        let path = parts.nth(2);
        if path == Some(exe) && offset.map(|o| u64::from_str_radix(o, 16) == Ok(0)).unwrap_or(false) {
            let start = range.and_then(|r| r.split('-').next()).ok_or("bad maps line")?;
            return Ok(u64::from_str_radix(start, 16)?);
        }
    }
    Err("the executable is not in /proc/self/maps".into())
}

/// (offset, bytes) of every file range the parser reads that the loader does not map.
fn unmapped_ranges(file: &mut File, h: &Header) -> Result<Vec<(u64, Vec<u8>)>, Box<dyn std::error::Error>> {
    let table_len = h.shentsize as usize * h.shnum as usize;
    let table = read_at(file, h.shoff, table_len)?;
    let mut ranges = vec![];
    for i in 0..h.shnum as usize {
        let s = &table[i * h.shentsize as usize..];
        let (kind, flags, offset, size) = (u32_at(s, 4), u64_at(s, 8), u64_at(s, 0x18), u64_at(s, 0x20));
        if flags & SHF_ALLOC == 0 && kind != SHT_NOBITS && size > 0 {
            ranges.push((offset, read_at(file, offset, size as usize)?));
        }
    }
    ranges.push((h.shoff, table));
    Ok(ranges)
}

/// Base address (to pass to `Image::read`) and the bytes to parse.
pub fn image_bytes() -> Result<(u64, &'static [u8]), Box<dyn std::error::Error>> {
    static PATCHED: OnceLock<(u64, &'static [u8])> = OnceLock::new();
    if let Some(cached) = PATCHED.get() {
        return Ok(*cached);
    }
    let exe = std::fs::read_link("/proc/self/exe")?;
    let exe_str = exe.to_str().ok_or("non-UTF-8 executable path")?;
    let mut file = File::open(&exe)?;
    let h = header(&mut file)?;
    let phdrs = read_at(&mut file, h.phoff, h.phentsize as usize * h.phnum as usize)?;
    let (mut start, mut end) = (u64::MAX, 0u64);
    let mut loads = vec![];
    for i in 0..h.phnum as usize {
        let p = &phdrs[i * h.phentsize as usize..];
        if u32_at(p, 0) == PT_LOAD {
            let (vaddr, memsz) = (u64_at(p, 0x10), u64_at(p, 0x28));
            start = start.min(vaddr);
            end = end.max(vaddr + memsz);
            loads.push((vaddr, memsz));
        }
    }
    if start == u64::MAX {
        return Err("no PT_LOAD in the executable".into());
    }
    // Non-PIE: the first mapping is at the vaddr itself and the base is 0; PIE: the difference.
    let base = mapping_start(exe_str)? - (start & !0xFFF);
    let len = (end - start) as usize;
    let memory: &'static [u8] = unsafe { std::slice::from_raw_parts((base + start) as *const u8, len) };

    let ranges = unmapped_ranges(&mut file, &h)?;
    // Only what a PT_LOAD maps is readable: old binaries align segments to 2 MiB and leave holes
    // between them, and touching a hole kills the process (Battalion, Mordhau).
    let mapped = |off: u64, n: usize| {
        let (a, b) = (start + off, start + off + n as u64);
        loads.iter().any(|(v, m)| a >= *v && b <= (*v + *m + 0xFFF) & !0xFFF)
    };
    // Memory serves as is only if every range is inside it, mapped, and already equal to the file.
    // A range past the last segment (Citadel: the section headers lie beyond the mapped image) or
    // in a hole has to come from the copy - left out, the parser reads past the slice and fails.
    let in_memory = |off: u64, n: usize| (off as usize).checked_add(n).is_some_and(|e| e <= len) && mapped(off, n);
    let matches = ranges.iter().all(|(off, bytes)| in_memory(*off, bytes.len())
        && &memory[*off as usize..*off as usize + bytes.len()] == bytes.as_slice());
    if matches {
        return Ok((base, memory));
    }
    let needed = ranges.iter().map(|(off, bytes)| *off as usize + bytes.len()).fold(len, usize::max);
    let mut copy = vec![0u8; needed];
    for (vaddr, memsz) in &loads {
        let (from, to) = ((vaddr - start) as usize, ((vaddr + memsz - start) as usize).min(len));
        copy[from..to].copy_from_slice(&memory[from..to]);
    }
    for (off, bytes) in &ranges {
        copy[*off as usize..*off as usize + bytes.len()].copy_from_slice(bytes);
    }
    let leaked: &'static [u8] = Box::leak(copy.into_boxed_slice());
    Ok(*PATCHED.get_or_init(|| (base, leaked)))
}
