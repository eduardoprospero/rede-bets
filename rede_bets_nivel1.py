# -*- coding: utf-8 -*-
"""
rede_bets_nivel1.py - monta o grafo bipartido (Bet -- Sócio) do nível 1 a partir de
nivel1_socios.csv + diagnostico_bets.csv e exporta:

  * rede_nivel1.gexf   -> abre direto no Gephi (e no RedeCNPJ NÃO: ver observação abaixo)
  * rede_nivel1.json   -> nós/arestas/estatísticas (usado pela visualização HTML)

Uso:
    python rede_bets_nivel1.py                       # lê de dados/processado/
    python rede_bets_nivel1.py --dir caminho/da/pasta

Convenções de chave (as mesmas do restante do pipeline):
  * Bet        -> "B:<cnpj14>"
  * Sócio PJ   -> "J:<raiz do CNPJ, 8 caracteres>"   (coluna socio_basico)
  * Sócio PF   -> "P:<socio_chave>"  = nome + CPF mascarado (***DDDDDD**)

LIMITAÇÃO conhecida: a Receita mascara o CPF (só 6 dígitos centrais). Duas pessoas
diferentes com o MESMO nome e os mesmos 6 dígitos seriam fundidas num nó só. É improvável,
mas não é impossível; não há como eliminar esse risco só com dados abertos.

LIMITAÇÃO conhecida 2: em S.A. o QSA lista administradores/diretores/conselheiros, NÃO
acionistas. Por isso cada aresta leva o atributo `vinculo` = 'administracao' (S.A.) ou
'societario' (demais). Não trate as duas como "controle acionário".
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import networkx as nx
import pandas as pd


def carregar(pasta: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = pd.read_csv(pasta / "nivel1_socios.csv", dtype=str, encoding="utf-8-sig").fillna("")
    d = pd.read_csv(pasta / "diagnostico_bets.csv", dtype=str, encoding="utf-8-sig").fillna("")
    return n, d


def capital_num(txt: str) -> float | None:
    try:
        return float(txt.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def data_br(aaaammdd: str) -> str:
    s = (aaaammdd or "").strip()
    return f"{s[6:8]}/{s[4:6]}/{s[0:4]}" if len(s) == 8 and s.isdigit() else ""


def fmt_cnpj(c: str) -> str:
    return f"{c[0:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else c


def construir(n: pd.DataFrame, d: pd.DataFrame) -> tuple[nx.Graph, dict]:
    G = nx.Graph()
    bets_basicos = set(d["cnpj_basico"])

    for _, r in d.iterrows():
        G.add_node(
            "B:" + r["cnpj"], tipo="bet", label=r["razao_spa"], cnpj=fmt_cnpj(r["cnpj"]),
            eh_sa=(r["eh_sa"] == "True"), n_marcas=int(r["n_marcas"]),
            n_autorizacoes=int(r["n_autorizacoes"]), natureza=r["natureza_desc"],
            capital_social=capital_num(r["capital_social"]) or 0.0,
        )

    for _, r in n.iterrows():
        bet = "B:" + r["cnpj_bet"]
        sa = G.nodes[bet]["eh_sa"]
        if r["tipo_socio"] == "PF":
            sid, tipo = "P:" + r["socio_chave"], "pf"
        elif r["tipo_socio"] == "PJ":
            sid, tipo = "J:" + r["socio_basico"], "pj"
        else:  # ESTRANGEIRO (não ocorre nos dados atuais, mas o layout da Receita prevê)
            sid, tipo = "E:" + r["socio_chave"], "estrangeiro"
        if sid not in G:
            G.add_node(sid, tipo=tipo, label=r["nome_socio"],
                       doc=r["cnpj_cpf_socio"], eh_bet=(r["socio_basico"] in bets_basicos))
        G.add_edge(bet, sid, qualificacao=r["qualificacao_desc"],
                   entrada=data_br(r["data_entrada_sociedade"]),
                   vinculo="administracao" if sa else "societario")

    # atributos derivados
    for nid, dat in G.nodes(data=True):
        if dat["tipo"] != "bet":
            dat["n_bets"] = len(list(G.neighbors(nid)))
    for comp in nx.connected_components(G):
        nb = sum(1 for x in comp if x.startswith("B:"))
        for x in comp:
            G.nodes[x]["comp_bets"] = nb
            G.nodes[x]["comp_id"] = min(comp)

    comps = list(nx.connected_components(G))
    stats = {
        "bets": int(sum(1 for _, a in G.nodes(data=True) if a["tipo"] == "bet")),
        "pf_distintos": int(sum(1 for _, a in G.nodes(data=True) if a["tipo"] == "pf")),
        "pj_distintos": int(sum(1 for _, a in G.nodes(data=True) if a["tipo"] == "pj")),
        "arestas": G.number_of_edges(),
        "componentes": len(comps),
        "componentes_2mais_bets": int(sum(1 for c in comps if sum(x.startswith("B:") for x in c) >= 2)),
        "pf_em_mais_de_uma_bet": int(sum(1 for _, a in G.nodes(data=True) if a["tipo"] == "pf" and a["n_bets"] > 1)),
        "pj_em_mais_de_uma_bet": int(sum(1 for _, a in G.nodes(data=True) if a["tipo"] == "pj" and a["n_bets"] > 1)),
        "pj_que_sao_bets": int(sum(1 for _, a in G.nodes(data=True) if a["tipo"] == "pj" and a.get("eh_bet"))),
        "bets_sa": int(sum(1 for _, a in G.nodes(data=True) if a["tipo"] == "bet" and a["eh_sa"])),
    }
    return G, stats


def exportar(G: nx.Graph, stats: dict, pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    H = G.copy()
    for _, a in H.nodes(data=True):  # GEXF só aceita tipos simples
        for k, v in list(a.items()):
            if isinstance(v, bool):
                a[k] = int(v)
    nx.write_gexf(H, pasta / "rede_nivel1.gexf")
    dados = {
        "stats": stats,
        "nodes": [{"id": i, **a} for i, a in G.nodes(data=True)],
        "edges": [{"from": u, "to": v, **a} for u, v, a in G.edges(data=True)],
    }
    (pasta / "rede_nivel1.json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=Path("dados/processado"))
    ap.add_argument("--saida", type=Path, default=None, help="pasta de saída (padrão: a mesma de --dir)")
    args = ap.parse_args()

    n, d = carregar(args.dir)
    G, stats = construir(n, d)
    exportar(G, stats, args.saida or args.dir)

    print("=== Rede nível 1 ===")
    for k, v in stats.items():
        print(f"{k:28s} {v}")
    print(f"Arquivos gravados em {args.saida or args.dir}")


if __name__ == "__main__":
    main()