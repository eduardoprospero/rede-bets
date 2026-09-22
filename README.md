# Rede de Propriedade das Bets no Brasil

Pipeline de dados abertos (SPA/MF + Receita Federal) para reconstruir, em forma de grafo,
quem são os sócios e as holdings por trás das operadoras de apostas de quota fixa
autorizadas no Brasil.

---

## 1. Objetivo do estudo

O projeto investiga a estrutura de propriedade e controle societário das operadoras de
apostas de quota fixa autorizadas pela Secretaria de Prêmios e Apostas (SPA/MF), buscando
responder:

- O mercado é concentrado em poucos grupos econômicos?
- Existem holdings e sócios em comum controlando múltiplas marcas/CNPJs?
- Há conexões entre as bets e outros setores (mídia, futebol, fintechs)?

A abordagem segue a linha metodológica do artigo *"The Network of Global Corporate
Control"* (Vitali, Glattfelder & Battiston, 2011) — citação que consta no resumo do
próprio projeto; não confirmei essa referência de forma independente, então vale conferir
o título/autoria antes de citá-la formalmente.

O pipeline segue 4 etapas conceituais:

1. **Coletar** — lista de CNPJs autorizados pela SPA/MF.
2. **Cruzar** — quadro de sócios (QSA) de cada CNPJ na base pública da Receita Federal.
3. **Expandir** — sócios pessoa jurídica (holdings), para revelar controladores indiretos
   (nível 1 no código atual; o texto do projeto menciona ferramentas como a *RedeCNPJ*
   para expansão adicional, que não está implementada nos scripts deste repositório).
4. **Modelar** — grafo bipartido Empresa–Sócio e suas projeções (Empresa–Empresa,
   Pessoa–Pessoa), com métricas de centralidade e detecção de comunidades.

**Importante (limitação estrutural dos dados, não do código):** em Sociedades Anônimas o
QSA da Receita Federal lista **administradores/diretores/conselheiros**, não acionistas.
O código sinaliza isso explicitamente (`natureza_desc`, campo `eh_sa`, atributo de aresta
`vinculo = "administracao"` vs. `"societario"`), e o grafo/HTML desenha essas arestas
tracejadas. Não trate vínculos de administração como participação acionária.

---

## 2. Estrutura de pastas esperada

```
projeto/
├── config.py                 # config central usada por 02, 04, 05
├── common.py                  # utilitários usados por 01 (lista_spa.py)
├── lista_spa.py               # etapa 1 — trata a lista da SPA
├── download_receita.py        # etapa 2 — baixa dados abertos do CNPJ
├── load_receita_db.py         # etapa 3 — carrega os zips no SQLite
├── cross_spa_receita.py       # etapa 4 — cruza SPA x Receita (nível 1)
├── coverage_receita.py        # etapa opcional de diagnóstico do cruzamento de dados
├── rede_bets_nivel1.py        # etapa 5 — monta o grafo
├── rede_bets_nivel1.html      # visualização interativa (dados embutidos)
├── requirements.txt
└── dados/
    ├── planilha-de-autorizacoes-1.csv        # entrada manual (ver seção 4, passo 1)
    ├── ProcessosjudiciaisSPA04.02.26.csv      # opcional, entrada manual
    ├── spa/                                   # saída da etapa 1
    ├── rfb_zip/AAAA-MM/                       # saída da etapa 2 (zips brutos da Receita)
    └── processado/                            # saída das etapas 3 a 5 (sqlite, csv, gexf, json)
```

---

## 3. Pré-requisitos

- Python 3.10+ (o código usa `str | None`, sintaxe de union types)
- Espaço em disco considerável: os zips completos de sócios/empresas/estabelecimentos da
  Receita Federal somam vários gigabytes por competência mensal.

Instale as dependências:

```bash
pip install -r requirements.txt
```

`requirements.txt` contém: `pandas`, `numpy`, `requests`, `openpyxl`, `networkx`.

---

## 4. Passo a passo de execução

### Passo 1 — Obter a lista de autorizações da SPA/MF (manual)

Não há, entre os scripts fornecidos, um download automático desse CSV — `lista_spa.py`
espera o arquivo **já presente** em `dados/planilha-de-autorizacoes-1.csv`. Baixe-o
manualmente na página indicada em `config.py` (`SPA_PAGINA_URL`):

```
https://www.gov.br/fazenda/pt-br/composicao/orgaos/secretaria-de-premios-e-apostas/
transparencia-ativa-processos-de-autorizacao-de-apostas-de-quota-fixa/empresas-autorizadas
```

Rode:

```bash
python lista_spa.py
```

Saídas em `dados/spa/`: `autorizacoes_spa.csv`, `bets_empresas_integrada.csv`,
`bets_marcas.csv`, `meta_spa.json`. O script também imprime um relatório com CNPJs
duplicados e dígitos verificadores inválidos.

### Passo 2 — Baixar os dados abertos do CNPJ (Receita Federal)

```bash
python download_receita.py --listar          # só lista competências e arquivos disponíveis
python download_receita.py                   # baixa a competência mais recente (tipos padrão)
python download_receita.py --mes 2026-08 --tipos socios dominios
python download_receita.py --verificar        # testa integridade dos zips já baixados
```

Isso grava os `.zip` em `dados/rfb_zip/AAAA-MM/` e um `manifesto.json` com o que foi
baixado. O código comenta que o endereço WebDAV e o token de compartilhamento
(`RFB_SHARE_TOKEN`) vêm de um projeto de terceiros e podem mudar; se o download falhar,
ajuste via `--token` ou pela variável de ambiente `RFB_SHARE_TOKEN`.

### Passo 3 — Carregar os zips no banco SQLite

```bash
python load_receita_db.py
```

Usa automaticamente a competência local mais recente em `dados/rfb_zip/`. Cria/recria
`dados/processado/bets_rede.sqlite` com as tabelas `empresas`, `estabelecimentos`,
`socios`, `dom_naturezas`, `dom_qualificacoes` e `meta`.

### Passo 4 (opcional, recomendado) — Diagnosticar cobertura

```bash
python coverage_receita.py --rapido     # checagem rápida de estrutura
python coverage_receita.py              # varredura completa
```

Ajuda a entender por que eventuais CNPJs da SPA não aparecem na Receita: raiz ausente dos
zips, falha na carga, ou mudança no layout dos arquivos.

### Passo 5 — Cruzar SPA x Receita (nível 1)

```bash
python cross_spa_receita.py
```

Gera em `dados/processado/`:
- `nivel1_socios.csv` — sócios diretos de cada bet (o mesmo arquivo que está anexado ao
  projeto);
- `diagnostico_bets.csv` — uma linha por bet, com indicadores como `achada_na_receita`,
  `eh_sa`, `ativa`, `nome_confere` (o mesmo arquivo anexado ao projeto);
- `diagnostico_resumo.json` — resumo com alertas (ex.: bets sem sócio no QSA, PF/PJ em mais
  de uma bet).

### Passo 6 — Montar o grafo

```bash
python rede_bets_nivel1.py --dir dados/processado
```

Lê `nivel1_socios.csv` + `diagnostico_bets.csv` e gera:
- `rede_nivel1.gexf` — para abrir no Gephi;
- `rede_nivel1.json` — nós, arestas e estatísticas, usado pela visualização HTML.

Convenção de identificadores dos nós: bet = `B:<cnpj14>`, sócio PJ = `J:<raiz 8 dígitos>`,
sócio PF = `P:<socio_chave>` (nome + 6 dígitos centrais do CPF, mascarado pela Receita).

### Passo 7 — Visualizar

Abra `rede_bets_nivel1.html` num navegador. 

Na página, é possível: buscar uma bet/sócio, filtrar PFs ligadas a uma só bet, filtrar
sócios PJ, mostrar só componentes com 2+ bets, e clicar em qualquer nó para ver seus
vínculos no painel lateral.

---

## 5. Limitações conhecidas (documentadas no próprio código)

- **QSA de S.A. ≠ acionistas.** Já mencionado acima; vale repetir porque muda a
  interpretação de boa parte do grafo (22 das 82 bets diagnosticadas são S.A., segundo o
  campo `bets_sa` do `rede_nivel1.json` incluído no projeto).
- **CPF mascarado.** A Receita expõe só os 6 dígitos centrais do CPF. Duas pessoas
  diferentes com mesmo nome e mesmos 6 dígitos seriam fundidas em um único nó. O código
  descreve isso como improvável, mas não descartável — não tenho como quantificar essa
  probabilidade.
- **Token e URLs da Receita/SPA podem mudar** a qualquer momento, já que dependem de
  compartilhamentos administrados por terceiros; os scripts falham com uma mensagem
  explicando onde obter o novo valor, mas isso exige intervenção manual quando ocorrer.

---

## 6. Arquivos de dados já incluídos no projeto

Dois arquivos anexados ao projeto (`nivel1_socios.csv` e `diagnostico_bets.csv`) já são
saídas de uma execução anterior do passo 5, com 418 linhas de vínculos societários e 82
bets diagnosticadas, respectivamente — úteis para explorar a etapa de modelagem (passo 6)
sem precisar refazer os passos 1 a 5 do zero.
