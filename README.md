# Extrator Universal com PKG

![Licença: GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-blue.svg)

`#gamer` `#gaming` `#games` `#jogos` `#gamebackup` `#gamecollection` `#ps4` `#ps4pkg` `#pkg` `#zip` `#rar` `#iso` `#7z` `#fileextractor` `#extratoruniversal` `#extrairtudo` `#python` `#tkinter` `#linux` `#kde` `#gameslibrary` `#gameinstaller` `#ultrafast` `#backup` `#arquivos`

Aplicativo Tkinter único para extrair **ZIP, RAR, ISO, 7z, TAR, CAB e ARJ**, além de **PKG de PS4**, com abas independentes, fila de arquivos, progresso detalhado (arquivos, bytes e ETA), senha, modo **Ultra rápida**, criação de pasta de destino e arrastar e soltar.

## Funcionalidades

- Interface gráfica única em Tkinter, com abas independentes.
- Fila de extração com progresso detalhado: arquivos processados, bytes transferidos e ETA.
- Suporte a senha para arquivos protegidos.
- Modo **Ultra rápida** para acelerar extrações com ajustes automáticos do motor.
- Opção **Nova pasta** para criar um diretório dedicado a cada extração.
- Arrastar e soltar de arquivos (com `tkinterdnd2`; se não estiver instalado, use o botão de seleção).
- Cancelamento por aba, sem interromper a extração das outras abas.
- Duas abas extraindo ao mesmo tempo: recurso suportado.
- Seleção automática de motor ou escolha manual.
- Registro de falhas em `erros.log`.

## Pré-requisitos

- **Python 3.10+** com Tkinter. Em Debian/Ubuntu: `sudo apt install python3-tk`; em Arch: `sudo pacman -S tk`.
- Pelo menos um dos motores de linha de comando: `7z`/`7zz`/`7za`, `unzip`, `unrar` ou `bsdtar`.
- Opcional: `tkinterdnd2` 0.7.0 para arrastar e soltar
  (`python3 -m pip install --user tkinterdnd2==0.7.0`).
- Opcional: motor **PKG externo** (`pkg_extractor`) para arquivos de PS4.

## Instalação

```sh
git clone https://github.com/gcunha1001/extrator-universal-com-pkg.git
cd extrator-universal-com-pkg
./install.sh
```

Instalação manual (alternativa): crie `~/.local/share/extrair-universal`,
`~/.local/bin` e `~/.local/share/applications`, copie `app.py` e `core.py` para o
primeiro diretório, copie o wrapper de inicialização para
`~/.local/bin/extrair-tudo` e o arquivo `.desktop` para
`~/.local/share/applications/`, marcando o wrapper como executável.

## Uso

Abra a entrada de menu **Extrair Tudo** ou execute:

```sh
~/.local/bin/extrair-tudo
```

- Cada aba mantém sua própria fila, progresso, log e cancelamento; destino, senha e Ultra rápida são compartilhados entre as abas. Adicione arquivos pelo botão de seleção ou arraste-os para a janela.
- **Destino**: diretório onde os arquivos serão extraídos. Por padrão, usa `XDG_DOCUMENTS_DIR` (ou `~/Documents`).
- **Nova pasta**: cria uma subpasta com o nome digitado pelo usuário dentro do destino atual e já a define como destino da extração.
- **Senha**: compartilhada entre as abas; vale para todos os arquivos da fila.
- **Ultra rápida**: ativa ajustes automáticos de velocidade do motor selecionado.
- **Cancelar**: encerra somente a extração da aba correspondente.
- É perfeitamente possível manter duas abas extraindo ao mesmo tempo.

## Motores

| Motor | Uso | Observação |
| --- | --- | --- |
| `auto` | Padrão | Escolhe o melhor motor disponível para cada formato. |
| `7z` | ZIP, RAR, ISO, 7z, TAR, CAB, ARJ | Requer `7z`, `7zz` ou `7za`. |
| `unzip` | ZIP | Requer `unzip`. |
| `unrar` | RAR | Requer `unrar`. |
| `bsdtar` | TAR, ZIP, ISO, CAB | Requer `bsdtar` (libarchive). |
| `pkg` | PKG de PS4 | Disponível somente quando o motor externo está instalado. |

## PKG de PS4 (opcional)

Este repositório **não inclui** o binário `pkg_extractor`. O pacote de código-fonte
correspondente (`vendor/shadps4plus` com `SOURCE.json`/ZIP) não acompanha a instalação
local; portanto, o mecanismo de extração de PKG é uma **dependência externa**.

Para habilitá-lo, instale o motor por conta própria a partir do projeto upstream
[ps4-pkg-extractor-ui](https://github.com/murums04/ps4-pkg-extractor-ui) (release
`PKG_EXTRACTOR_1_1`, commit `771a8d58aff540448c7cbace7c40a66a5daf010d`) e aponte
`EXTRATOR_PKG_DIR` para o diretório que contém o executável:

```sh
export EXTRATOR_PKG_DIR="$HOME/.local/share/extrator-pkg"
```

Sem o binário, os demais formatos continuam funcionando e o motor `pkg` aparece como
indisponível. Nenhum binário de terceiros é redistribuído neste repositório.

## Variáveis de ambiente

| Variável | Finalidade |
| --- | --- |
| `EXTRATOR_PKG_DIR` | Diretório com o executável externo `pkg_extractor`. |
| `PYTHON` | Interpretador Python usado pelo wrapper (padrão: `python3`). |
| `XDG_STATE_HOME` | Onde o aplicativo grava estado e `erros.log` (padrão: `~/.local/state`). |
| `XDG_DOCUMENTS_DIR` | Diretório de destino padrão (padrão: `~/Documents`). |
| `XLIB_SKIP_ARGB_VISUALS=1` | Contorno para problemas de visualização em Tk/X11. |

## Estrutura de arquivos

```text
extrator-universal-com-pkg/
├── app.py                      # interface Tkinter
├── core.py                     # motores, fila e progresso
├── extrair-tudo                # wrapper instalado em ~/.local/bin
├── extrair-tudo.desktop
├── install.sh
├── README.md
├── LICENSE                     # GPL-3.0-only
├── THIRD_PARTY_NOTICES.md
├── licenses/
│   ├── GPL-2.0.txt             # motor pkg_extractor (upstream)
│   └── tkinterdnd2-MIT.txt     # tkinterdnd2 0.7.0
└── .gitignore
```

## Licença

Código e adaptações deste repositório: **GPL-3.0-only**. Parte do código foi adaptada de
ps4-pkg-extractor-ui, Copyright 2026 murums (ver
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). Veja também [LICENSE](LICENSE).

## Aviso

Projeto **Linux-first**: usa `select` e X11. Testado em **KDE Plasma/XWayland**.
Em outros ambientes, pode ser necessário ajustar variáveis como
`XLIB_SKIP_ARGB_VISUALS=1`.
