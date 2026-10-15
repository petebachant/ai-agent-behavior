import sys, os, csv, json, math, re
from datetime import *

d = []
t = 0
t2 = 0
r = {}
p = {}
n = 0


def go():
    global d, t, t2, r, p, n
    f = open(sys.argv[1])
    lines = f.readlines()
    for i in range(len(lines)):
        if i == 0:
            continue
        l = lines[i].strip().split(",")
        if len(l) < 5:
            continue
        try:
            u = int(l[3])
        except:
            continue
        try:
            pr = float(l[4])
        except:
            continue
        amt = u * pr
        if u >= 50:
            amt = amt - amt * 0.1
        else:
            if u >= 10:
                amt = amt - amt * 0.05
        if l[1] == "north":
            tx = amt * 0.07
        elif l[1] == "south":
            tx = amt * 0.05
        elif l[1] == "east":
            tx = amt * 0.06
        elif l[1] == "west":
            tx = amt * 0.08
        else:
            tx = 0
        t = t + amt
        t2 = t2 + tx
        if l[1] in r:
            r[l[1]] = r[l[1]] + amt
        else:
            r[l[1]] = amt
        if l[2] in p:
            p[l[2]] = p[l[2]] + amt
        else:
            p[l[2]] = amt
        d.append(l)
        n = n + 1
    print("SALES REPORT")
    print("============")
    print("Rows: " + str(n))
    print("Total revenue: " + str(round(t, 2)))
    print("Total tax: " + str(round(t2, 2)))
    print("")
    print("By region:")
    for k in sorted(r.keys()):
        print("  " + k + ": " + str(round(r[k], 2)))
    print("")
    print("By product:")
    for k in sorted(p.keys()):
        print("  " + k + ": " + str(round(p[k], 2)))
    print("")
    best = None
    bv = 0
    for k in p:
        if p[k] > bv:
            bv = p[k]
            best = k
    print("Top product: " + best + " (" + str(round(bv, 2)) + ")")


go()
