# -*- coding: utf-8 -*-
"""
download_receita.py - baixa os dados abertos do CNPJ (Receita Federal).

Como funciona (desde a mudança da Receita em jan/2026)
------------------------------------------------------
Os arquivos ficam num compartilhamento Nextcloud público. O script:
  1. lista as competências (pastas AAAA-MM) por WebDAV (método PROPFIND);
  2. lista os .zip da competência escolhida (padrão: a mais recente);
  3. baixa só os tipos pedidos (padrão: empresas, estabelecimentos, socios e tabelas de domínio),
     com retomada de download interrompido e conferência de tamanho;
  4. grava dados/rfb_zip/AAAA-MM/manifesto.json com URL, tamanho e data do download.

Uso
---
    python download_receita.py --listar                 # só mostra competências e arquivos
    python download_receita.py                          # baixa a competência mais recente
    python download_receita.py --mes 2026-08 --tipos socios dominios
    python download_receita.py --verificar              # testa a integridade dos zips baixados

IMPORTANTE (transparência sobre o que não foi verificado)
---------------------------------------------------------
* O endereço WebDAV e o token de compartilhamento vêm de um projeto de terceiros
  (ver config.py). Se a Receita alterar, ajuste RFB_SHARE_TOKEN / --token.
* Os nomes exatos dos arquivos na competência atual não foram verificados por quem escreveu
  o script; por isso os tipos são reconhecidos por trechos do nome.
  Rode --listar antes e confira se cada tipo casou com os arquivos esperados.
* Respeite a Receita: poucos downloads paralelos (--paralelo, padrão 2).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import requests

import config as cfg

NS = {"d": "DAV:"}
TIPOS_PADRAO = ["empresas", "estabelecimentos", "socios", "cnaes", "motivos", "municipios",
                "naturezas", "paises", "qualificacoes"]


# --------------------------------------------------------------------------- #
# WebDAV
# --------------------------------------------------------------------------- #
def _sessao() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = cfg.USER_AGENT
    return s


def propfind(sess: requests.Session, url: str, token: str) -> ET.Element:
    r = sess.request("PROPFIND", url, auth=(token, ""), headers={"Depth": "1"}, timeout=(15, 120))
    r.raise_for_status()
    return ET.fromstring(r.content)


def itens(root: ET.Element) -> list[dict]:
    """Extrai (href, é_pasta, tamanho) de uma resposta multistatus do WebDAV."""
    saida = []
    for resp in root.findall("d:response", NS):
        href_el = resp.find("d:href", NS)
        if href_el is None or not href_el.text:
            continue
        tam_el = resp.find("d:propstat/d:prop/d:getcontentlength", NS)
        pasta = resp.find("d:propstat/d:prop/d:resourcetype/d:collection", NS) is not None
        saida.append({
            "href": unquote(href_el.text),
            "pasta": pasta,
            "bytes": int(tam_el.text) if tam_el is not None and tam_el.text and tam_el.text.isdigit() else None,
        })
    return saida


def listar_competencias(sess, base: str, token: str) -> list[str]:
    meses = []
    for it in itens(propfind(sess, base + "/", token)):
        m = re.search(r"(\d{4}-\d{2})/?$", it["href"])
        if m:
            meses.append(m.group(1))
    return sorted(set(meses))


def listar_zips(sess, base: str, token: str, mes: str) -> list[dict]:
    arquivos = []
    for it in itens(propfind(sess, f"{base}/{mes}/", token)):
        m = re.search(r"/([^/]+\.zip)$", it["href"], flags=re.IGNORECASE)
        if m and not it["pasta"]:
            arquivos.append({"nome": m.group(1), "bytes": it["bytes"]})
    return sorted(arquivos, key=lambda a: a["nome"])


# --------------------------------------------------------------------------- #
# Download com retomada
# --------------------------------------------------------------------------- #
def baixar_arquivo(url: str, destino: Path, esperado: int | None, token: str, tentativas: int = 4) -> dict:
    if destino.exists() and (esperado is None or destino.stat().st_size == esperado):
        return {"nome": destino.name, "bytes": destino.stat().st_size, "url": url, "status": "ja_existia"}

    parcial = destino.with_name(destino.name + ".part")
    sess = _sessao()
    ultimo_erro: Exception | None = None
    for t in range(1, tentativas + 1):
        try:
            pos = parcial.stat().st_size if parcial.exists() else 0
            headers = {"Range": f"bytes={pos}-"} if pos else {}
            r = sess.get(url, headers=headers, stream=True, timeout=(15, 120))
            if r.status_code in (401, 403):  # alguns compartilhamentos exigem o token como usuário
                r = sess.get(url, headers=headers, stream=True, timeout=(15, 120), auth=(token, ""))
            if r.status_code == 416:  # faixa inválida: o .part provavelmente já está completo
                r.close()
            else:
                r.raise_for_status()
                modo = "ab" if (r.status_code == 206 and pos) else "wb"
                with open(parcial, modo) as fh:
                    for bloco in r.iter_content(chunk_size=1024 * 1024):
                        if bloco:
                            fh.write(bloco)
            tamanho = parcial.stat().st_size
            if esperado is not None and tamanho != esperado:
                raise IOError(f"tamanho {tamanho} != esperado {esperado}")
            parcial.replace(destino)
            return {"nome": destino.name, "bytes": tamanho, "url": url, "status": "baixado"}
        except (requests.RequestException, IOError) as e:
            ultimo_erro = e
            if isinstance(e, IOError) and "esperado" in str(e) and parcial.exists():
                parcial.unlink()  # arquivo corrompido/maior que o esperado: recomeça do zero
            time.sleep(min(30, 2 ** t))
    raise RuntimeError(f"Falha ao baixar {destino.name}: {ultimo_erro}")


def verificar_zips(pasta: Path) -> bool:
    ok = True
    for z in sorted(pasta.glob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                ruim = zf.testzip()
            print(f"{'OK ' if ruim is None else 'RUIM'} {z.name}" + (f" (membro corrompido: {ruim})" if ruim else ""))
            ok &= ruim is None
        except zipfile.BadZipFile:
            print(f"RUIM {z.name} (zip inválido)")
            ok = False
    return ok


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mes", help="competência AAAA-MM (padrão: a mais recente publicada)")
    
    # Linha corrigida: usando TIPOS_PADRAO para choices ao invés de cfg.PADROES_ARQUIVO
    ap.add_argument("--tipos", nargs="+", default=TIPOS_PADRAO, choices=sorted(TIPOS_PADRAO),
                    help="tipos de arquivo a baixar")
    
    ap.add_argument("--listar", action="store_true", help="apenas lista competências/arquivos")
    ap.add_argument("--verificar", action="store_true", help="testa a integridade dos zips já baixados")
    ap.add_argument("--paralelo", type=int, default=2, help="downloads simultâneos (padrão 2)")
    ap.add_argument("--token", default=cfg.RFB_SHARE_TOKEN, help="token do compartilhamento Nextcloud")
    ap.add_argument("--base-webdav", default=cfg.RFB_BASE_WEBDAV)
    ap.add_argument("--base-arquivos", default=cfg.RFB_BASE_ARQUIVOS)
    args = ap.parse_args()

    cfg.garantir_pastas()

    if args.verificar:
        pasta = cfg.DIR_RFB_ZIP / args.mes if args.mes else cfg.ultima_competencia_local()
        if not pasta or not pasta.exists():
            sys.exit("Nenhuma pasta de competência local encontrada.")
        sys.exit(0 if verificar_zips(pasta) else 1)

    sess = _sessao()
    try:
        meses = listar_competencias(sess, args.base_webdav, args.token)
    except requests.RequestException as e:
        sys.exit(
            f"Falha ao listar competências: {e}\n"
            "Possíveis causas: token de compartilhamento trocado pela Receita, mudança de URL, "
            "ou bloqueio de rede. Veja o cabeçalho de config.py para saber como obter o token atual."
        )
    if not meses:
        sys.exit("Nenhuma pasta AAAA-MM encontrada no compartilhamento.")
    mes = args.mes or meses[-1]
    if mes not in meses:
        sys.exit(f"Competência {mes} não encontrada. Disponíveis: {', '.join(meses[-12:])}")

    zips = listar_zips(sess, args.base_webdav, args.token, mes)
    print(f"Competências disponíveis (últimas 6): {', '.join(meses[-6:])}")
    print(f"Competência escolhida: {mes}  ({len(zips)} arquivos .zip)")
    escolhidos = []
    for z in zips:
        tipo = cfg.classificar_arquivo(z["nome"])
        marca = "*" if tipo in args.tipos else " "
        print(f" {marca} {z['nome']:<45} {str(z['bytes'] or '?'):>14} bytes  tipo={tipo}")
        if tipo in args.tipos:
            escolhidos.append(z)
    for tipo in args.tipos:
        if not any(cfg.classificar_arquivo(z["nome"]) == tipo for z in zips):
            # Linha corrigida: removida a menção a config.PADROES_ARQUIVO
            print(f"[aviso] nenhum arquivo casou com o tipo '{tipo}'. Confira a função classificar_arquivo em config.py.", file=sys.stderr)
    if args.listar:
        return
    if not escolhidos:
        sys.exit("Nada para baixar com os tipos escolhidos.")

    destino_dir = cfg.DIR_RFB_ZIP / mes
    destino_dir.mkdir(parents=True, exist_ok=True)
    total = sum(z["bytes"] or 0 for z in escolhidos)
    print(f"\nBaixando {len(escolhidos)} arquivos (~{total / 1e9:.1f} GB) para {destino_dir}")

    resultados = []
    with ThreadPoolExecutor(max_workers=max(1, args.paralelo)) as ex:
        futs = {
            ex.submit(
                baixar_arquivo, f"{args.base_arquivos}/{args.token}/{mes}/{z['nome']}",
                destino_dir / z["nome"], z["bytes"], args.token,
            ): z for z in escolhidos
        }
        for fut in as_completed(futs):
            res = fut.result()
            resultados.append(res)
            print(f"  [{res['status']}] {res['nome']} ({res['bytes'] / 1e6:.0f} MB)")

    manifesto = {
        "competencia": mes,
        "baixado_em": datetime.now().isoformat(timespec="seconds"),
        "arquivos": sorted(resultados, key=lambda r: r["nome"]),
    }
    (destino_dir / "manifesto.json").write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nConcluído. Rode com --verificar para testar a integridade dos zips.")


if __name__ == "__main__":
    main()