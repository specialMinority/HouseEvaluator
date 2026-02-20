import csv
from collections import defaultdict

rows = list(csv.DictReader(open('benchmark_collection/phase2_structure_benchmarks.csv', encoding='utf-8')))
structs = ['wood','light_steel','steel','rc','src']
layouts = ['1R','1K','1DK','1LDK']

TOKYO_WARDS = ['千代田区','中央区','港区','新宿区','文京区','台東区','墨田区','江東区','品川区','目黒区','大田区','世田谷区','渋谷区','中野区','杉並区','豊島区','北区','荒川区','板橋区','練馬区','足立区','葛飾区','江戸川区']
OSAKA_WARDS = ['大阪市都島区','大阪市福島区','大阪市此花区','大阪市西区','大阪市港区','大阪市大正区','大阪市天王寺区','大阪市浪速区','大阪市西淀川区','大阪市東淀川区','大阪市東成区','大阪市生野区','大阪市旭区','大阪市城東区','大阪市阿倍野区','大阪市住吉区','大阪市東住吉区','大阪市西成区','大阪市淀川区','大阪市鶴見区','大阪市住之江区','大阪市平野区','大阪市北区','大阪市中央区']

collected = set()
for r in rows:
    collected.add((r['municipality'], r['layout_type'], r['building_structure']))

def coverage(wards, label):
    total = len(wards)*len(layouts)*len(structs)
    covered = sum(1 for w in wards for l in layouts for s in structs if (w,l,s) in collected)
    print(f'\n=== {label} ===')
    print(f'전체: {covered}/{total} ({100*covered//total}%)')
    print(f'구조별:')
    for s in structs:
        cnt = sum(1 for w in wards for l in layouts if (w,l,s) in collected)
        max_cnt = len(wards)*len(layouts)
        bar = '█' * (cnt*20//max_cnt) + '░' * (20 - cnt*20//max_cnt)
        print(f'  {s:12s}: {cnt:3d}/{max_cnt} [{bar}]')

coverage(TOKYO_WARDS, '도쿄')
coverage(OSAKA_WARDS, '오사카')
