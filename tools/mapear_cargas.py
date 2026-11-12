#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mapear_cargas.py - convierte las lineas "[carga]" del log (fh2_io_cargas) en nombres de archivo y en una
posicion aproximada del jugador.

El juego lee bin.zip con (offset, bytes). Cada offset cae dentro de una entrada del directorio central del zip,
asi que se puede saber QUE archivo (N.fiz, .pgeo, .pvsz...) pidio y CUANDO. Los mosaicos N.fiz llevan su celda en
Anthem_track_00.col, y de la celda sale su posicion en el mapa.

USO
  1) Una vez por cada bin.zip con el que se juega (el indice depende de los offsets de ESE zip):
       python mapear_cargas.py indice  bin.zip  indice.json
  2) Con el log de una sesion (jogos/fh2-logs/fh2_AAAAMMDD_HHMMSS.log), jugado con "Log detalhado" activado:
       python mapear_cargas.py mapear  indice.json  fh2_AAAAMMDD_HHMMSS.log  [--col=Anthem_track_00.col]
              [--csv=cargas.csv] [--json=telarana.json] [--ventana=5]

LIMITES (leer):
  - El juego puede leer bloques mas grandes que una entrada: una lectura puede cubrir varias entradas. Se listan
    todas las que solapan; no se puede saber cuales usa de verdad.
  - La posicion es la mediana de los centros de los mosaicos pedidos en cada ventana de tiempo. El juego carga
    por delante del coche, asi que sale adelantada respecto a donde estas. Es una estimacion de ~100 m, no una
    coordenada.
  - El indice y el log deben ser del MISMO zip (si lo reempaquetaste, los offsets cambian).
Solo usa la libreria estandar de Python 3.
"""
import bisect, json, mmap, re, struct, sys
from collections import Counter, defaultdict

# Cuadricula de Anthem_track_00.col (ver PUERTO_NIZA.md, seccion 3.2/3.4)
OX, OZ, NC = -4502.7, -8856.4, 154
TILE = 116.0          # lado de un mosaico .fiz; 8 m de margen por lado sobre la celda de 100 m
MARGEN = 8.0


def leer_directorio(ruta):
    """Devuelve [(nombre, doff, csize)] de un zip FH2: sin cabeceras locales, contador EOCD desbordado."""
    with open(ruta, "rb") as f:
        zm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            e = zm.rfind(b"PK\x05\x06")
            if e < 0:
                raise SystemExit("no es un zip (sin EOCD)")
            (_, _, _, _, _, cdsize, cdoff, _) = struct.unpack("<4s4H2IH", zm[e:e + 22])
            fin = cdoff + cdsize if cdoff + cdsize <= e else e
            salida, p, omitidas = [], cdoff, 0
            while p + 46 <= fin:
                (sig, _, _, _, meth, _, _, _, csize, usize, nlen, elen, cmlen, _, _, _, lho) = \
                    struct.unpack("<4sHHHHHHIIIHHHHHII", zm[p:p + 46])
                if sig != b"PK\x01\x02":
                    raise SystemExit("directorio central corrupto en %d" % p)
                nombre = zm[p + 46:p + 46 + nlen].decode("utf-8", "replace")
                p += 46 + nlen + elen + cmlen
                if csize == 0xFFFFFFFF or lho == 0xFFFFFFFF:
                    omitidas += 1          # zip64: no aparece en los bin.zip de FH2
                    continue
                # Sin cabecera local los datos empiezan en lho; con ella, tras la cabecera (30 + nombre + extra)
                if zm[lho:lho + 4] == b"PK\x03\x04":
                    lnl, lel = struct.unpack("<HH", zm[lho + 26:lho + 30])
                    doff = lho + 30 + lnl + lel
                else:
                    doff = lho
                salida.append((nombre, doff, csize))
            if omitidas:
                print("aviso: %d entradas zip64 omitidas" % omitidas)
            return salida
        finally:
            zm.close()


def cmd_indice(zip_ruta, salida):
    ent = leer_directorio(zip_ruta)
    orden = sorted(range(len(ent)), key=lambda i: ent[i][1])
    datos = {"origen": zip_ruta, "n": len(ent),
             "nombres": [ent[i][0] for i in orden],
             "doff": [ent[i][1] for i in orden],
             "csize": [ent[i][2] for i in orden]}
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(datos, f)
    print("indice: %d entradas -> %s" % (len(ent), salida))


def cargar_col(ruta):
    """id de mosaico -> (celda, x0, z0). Registros de 24 B desde 0x54; el conteo esta en 0x4c (big-endian)."""
    b = open(ruta, "rb").read()
    n = struct.unpack(">I", b[0x4c:0x50])[0]
    mosaicos = {}
    for k in range(n):
        _, ident, celda, _, _, _ = struct.unpack(">6I", b[84 + 24 * k:108 + 24 * k])
        fila, col = divmod(celda, NC)
        mosaicos[ident] = (celda, OX + col * 100.0 - MARGEN, OZ + fila * 100.0 - MARGEN)
    return mosaicos


RE_CARGA = re.compile(r"\[carga\] t=(\d+) ms bytes=(\d+) offset=(\d+)")
RE_FIZ = re.compile(r"(?:^|[\\/])(\d+)\.fiz$", re.I)


def mediana(v):
    v = sorted(v)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0


def cmd_mapear(idx_ruta, log_ruta, opc):
    idx = json.load(open(idx_ruta, encoding="utf-8"))
    nombres, doff, csize = idx["nombres"], idx["doff"], idx["csize"]
    col = cargar_col(opc["col"]) if "col" in opc else None
    ventana = float(opc.get("ventana", 5)) * 1000.0

    lecturas = []
    for linea in open(log_ruta, encoding="utf-8", errors="replace"):
        m = RE_CARGA.search(linea)
        if m:
            lecturas.append(tuple(int(x) for x in m.groups()))
    if not lecturas:
        raise SystemExit("el log no tiene lineas [carga]: ¿Log detalhado activo y version con el parche?")

    filas, por_ext, por_ventana, sin_entrada = [], Counter(), defaultdict(list), 0
    for t, nbytes, off in lecturas:
        fin = off + max(nbytes, 1)
        i = max(bisect.bisect_right(doff, off) - 1, 0)
        halladas = []
        while i < len(doff) and doff[i] < fin:
            if csize[i] > 0 and doff[i] + csize[i] > off:
                halladas.append(i)
            i += 1
        if not halladas:
            sin_entrada += 1
        for j in halladas:
            nombre = nombres[j]
            ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else "(sin ext)"
            por_ext[ext] += 1
            mz = RE_FIZ.search(nombre)
            if mz:
                por_ventana[int(t // ventana)].append(int(mz.group(1)))
        filas.append((t, off, nbytes, [nombres[j] for j in halladas]))

    if "csv" in opc:
        with open(opc["csv"], "w", encoding="utf-8") as f:
            f.write("t_ms;offset;bytes;n_entradas;nombres\n")
            for t, off, nb, nn in filas:
                f.write("%d;%d;%d;%d;%s\n" % (t, off, nb, len(nn), "|".join(nn[:8])))
        print("csv:", opc["csv"])

    ventanas = []
    for k in sorted(por_ventana):
        ids = sorted(set(por_ventana[k]))
        v = {"t0_s": k * ventana / 1000.0, "t1_s": (k + 1) * ventana / 1000.0, "mosaicos": ids}
        if col:
            pts = [(col[i][1] + TILE / 2, col[i][2] + TILE / 2) for i in ids if i in col]
            if pts:
                v["centro"] = [round(mediana([p[0] for p in pts]), 1), round(mediana([p[1] for p in pts]), 1)]
                v["n_con_celda"] = len(pts)
        ventanas.append(v)
    if "json" in opc:
        json.dump({"ventana_s": ventana / 1000.0, "ventanas": ventanas}, open(opc["json"], "w"), indent=1)
        print("json:", opc["json"])

    print("lecturas [carga]: %d (sin entrada del zip: %d)" % (len(lecturas), sin_entrada))
    print("entradas leidas por extension:", ", ".join("%s=%d" % kv for kv in por_ext.most_common(12)))
    if ventanas:
        print("\nventana(s)  mosaicos distintos  centro estimado (x, z)")
        for v in ventanas[:40]:
            print("  %6.0f-%-6.0f s   %4d   %s" % (v["t0_s"], v["t1_s"], len(v["mosaicos"]), v.get("centro", "-")))
        if len(ventanas) > 40:
            print("  ... (%d ventanas; ver --json)" % len(ventanas))


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    opc = dict((a[2:].split("=", 1) + [""])[:2] for a in sys.argv[1:] if a.startswith("--"))
    if len(pos) == 3 and pos[0] == "indice":
        cmd_indice(pos[1], pos[2])
    elif len(pos) == 3 and pos[0] == "mapear":
        cmd_mapear(pos[1], pos[2], opc)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
