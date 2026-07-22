#!/usr/bin/env python3
"""테스트용 영문 샘플 PDF 생성: python examples/make_sample_pdf.py"""
import pymupdf
from pathlib import Path

OUT = Path(__file__).resolve().parent / "sample_en.pdf"

doc = pymupdf.open()
page = doc.new_page(width=595, height=842)  # A4

# 제목 (굵게, 20pt)
page.insert_textbox(pymupdf.Rect(60, 55, 540, 95),
    "VXLAN and EVPN: A Practical Overview",
    fontsize=20, fontname="hebo")

# 본문 문단 1
page.insert_textbox(pymupdf.Rect(60, 105, 540, 185),
    "Virtual Extensible LAN (VXLAN) is an overlay encapsulation protocol that "
    "extends Layer 2 segments across a Layer 3 underlay network. Each segment is "
    "identified by a 24-bit VXLAN Network Identifier (VNI), which allows up to "
    "16 million logical networks on a shared physical fabric.",
    fontsize=11, fontname="helv")

# 본문 문단 2
page.insert_textbox(pymupdf.Rect(60, 195, 540, 275),
    "Ethernet VPN (EVPN) provides the control plane for VXLAN. Instead of "
    "flood-and-learn, MAC and IP reachability information is distributed between "
    "VTEPs using Multiprotocol BGP. This reduces unnecessary flooding and enables "
    "active-active multihoming in leaf-spine designs.",
    fontsize=11, fontname="helv")

# 소제목
page.insert_textbox(pymupdf.Rect(60, 290, 540, 315),
    "Key Components", fontsize=14, fontname="hebo")

# 불릿 목록
page.insert_textbox(pymupdf.Rect(60, 320, 540, 400),
    "- Underlay: provides IP reachability between loopbacks (OSPF or eBGP)\n"
    "- Overlay: the EVPN address family carries MAC and IP routes\n"
    "- Data plane: UDP encapsulation with default destination port 4789",
    fontsize=11, fontname="helv")

# 숫자 전용 라인 (번역 생략 휴리스틱 검증용)
page.insert_textbox(pymupdf.Rect(60, 415, 540, 435),
    "4789     24     16777216", fontsize=10, fontname="helv")

# 색상 있는 주의문 (색/작은 폰트 보존 검증용)
page.insert_textbox(pymupdf.Rect(60, 450, 540, 495),
    "Note: Verify the MTU on the underlay before migration. VXLAN adds "
    "50 bytes of overhead, so an underlay MTU of at least 1550 is required.",
    fontsize=9, fontname="helv", color=(0.75, 0.15, 0.1))

# 페이지 번호
page.insert_textbox(pymupdf.Rect(280, 800, 320, 820), "1",
    fontsize=9, fontname="helv")

doc.save(OUT)
print(f"saved: {OUT}")
