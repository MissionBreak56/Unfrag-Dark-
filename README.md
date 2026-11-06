# Unfrag Dark

<img src="docs/icon.png" width="96" align="right" alt="Ícone do Unfrag Dark">

Desfragmentador de disco **NTFS** para Windows, de código aberto, com **tema escuro**.
Projeto independente, escrito do zero em Python (sem código, nome, ícones ou textos de terceiros).
Foi inspirado, em termos de *funcionalidades*, em desfragmentadores clássicos para PC.

![Análise](docs/screenshot-analyze.png)
![Depois de desfragmentar](docs/screenshot-defrag.png)
![Opções com explicações](docs/screenshot-options.png)

> As capturas usam o **modo demo** (volumes simulados), por isso funcionam em qualquer sistema.

## Funções

| Função | GUI | CLI |
|---|---|---|
| Listar unidades (tipo HDD/SSD, sistema de arquivos, espaço livre) | ✔ | `unfrag list` |
| Analisar fragmentação sem alterar nada | Analisar | `unfrag analyze C:` |
| Desfragmentar (completo) | Desfragmentar | `unfrag defrag C:` |
| Desfragmentação rápida (arquivos até 256 MiB) | Rápida | `--mode quick` |
| Consolidar espaço livre | Espaço livre | `--mode freespace` |
| Desfragmentar uma pasta ou um arquivo | Opções / botão direito na lista | `--path "C:\Pasta"` |
| Mover arquivos grandes / por extensão para o fim do disco | Opções | `--move-large 500` / `--move-ext .iso,.vhdx` |
| Mapa de disco colorido (livre / em uso / fragmentado / não movível) | ✔ | — |
| Lista de arquivos fragmentados (ordenável) | ✔ | — |
| Proteção para SSD (ignora, a menos que forçado) | ✔ | `--force-ssd` |
| Threads de leitura da análise (1–64, 0 = automático) | Opções | `--threads N` |
| Só executar se a fragmentação for ≥ X% | ✔ | `--min-frag 10` |
| Não executar na bateria / se certos processos estiverem rodando | ✔ | `--skip-battery` / `--exclude app.exe` |
| Desligar o PC ao terminar | ✔ | `--shutdown` |
| Agendamento (diário/semanal/mensal) via Agendador de Tarefas | Agendamento | `unfrag schedule create C:` |
| Parar a qualquer momento (cancelamento seguro) | Parar | Ctrl+C |

## Como funciona

Usa as IOCTLs documentadas do Windows para desfragmentação online, as mesmas que a Microsoft
descreve na documentação de *Defragmenting Files*:

* `FSCTL_GET_VOLUME_BITMAP` – mapa de clusters livres/ocupados;
* `FSCTL_GET_RETRIEVAL_POINTERS` – extents de cada arquivo;
* `FSCTL_MOVE_FILE` – o próprio NTFS move os clusters, mantendo a integridade dos dados;
* `IOCTL_STORAGE_QUERY_PROPERTY` – detecta SSD (sem penalidade de seek).

O algoritmo (em `src/unfrag/engine.py`) é próprio: arquivos fragmentados são regravados em uma única
corrida livre (first-fit, menores primeiro); o modo *espaço livre* ainda puxa arquivos do fim do volume
para os primeiros buracos, em até 4 passadas; arquivos grandes podem ser empurrados para o fim.
Arquivos de sistema, esparsos, criptografados (EFS), compactados pelo NTFS e pontos de reanálise
nunca são tocados.

## Instalação e uso

Requer Windows 10/11 e Python 3.10+ (ou use o `.exe` gerado pelo CI em *Actions → build → Artifacts*).

```bat
pip install .
unfrag gui                 :: interface gráfica (execute como Administrador)
unfrag list
unfrag analyze C:
unfrag defrag C: --mode quick
unfrag defrag D: --mode freespace --move-large 1000 --skip-battery
unfrag schedule create C: --frequency semanal --time 03:00 --mode quick
```

Teste sem risco em qualquer sistema (inclusive Linux/macOS):

```bash
pip install .
unfrag --demo gui
unfrag --demo defrag C: --mode freespace
```

## Velocidade da análise

* Os arquivos são lidos em **várias threads** (escolha em *Opções → Threads de leitura*, `--threads N` na CLI ou `UNFRAG_THREADS=N`; padrão automático: 2 por núcleo, até 16) —
  a espera pelo disco libera o GIL, então a leitura de extents é paralela.
* Arquivos que cabem em **1 cluster não podem fragmentar** e são apenas contados, sem abrir o arquivo.
* O mapa de clusters livres é interpretado por blocos homogêneos (≈10× mais rápido que bit a bit).
* Buffers reaproveitados por thread e caminhos longos sem `abspath` por arquivo.
* O coletor de lixo do Python fica **pausado durante a varredura** (milhões de objetos pequenos sem
  ciclos; o GC os reexaminava várias vezes) e o buffer de extents é decodificado de uma vez
  (`ntfs.py`), sem copiar o buffer nem fazer um `struct` por extent. Em um benchmark sintético
  (300 mil arquivos, só CPU em Python, sem E/S) a análise caiu de ~2,7 s para ~1,3 s.
* Objetos ctypes (buffer, `c_int64`, `DWORD`, `byref`) são reaproveitados por thread.
* A janela e a CLI mostram o tempo da análise.
* **Leitura direta da MFT** (padrão; `--no-mft` ou Opções desligam): a análise do volume inteiro lê a MFT do NTFS
  em sequência em vez de abrir cada arquivo. No Windows de teste do CI (1,18 milhão de arquivos) levou 20 s contra
  9 min 24 s do método clássico, com os mesmos extents. Se algo não puder ser lido com segurança, aquele arquivo (ou
  o volume todo) volta ao método clássico.
* **Iniciar com o Windows** (Opções): tarefa agendada de logon com privilégios altos, sem pedir confirmação a cada
  login, abre minimizado na bandeja. O ícone da bandeja muda de cor (laranja = trabalhando, verde = concluído,
  vermelho = erro) e as tarefas agendadas avisam ao terminar.
* **Atualizações:** o programa verifica sozinho se há versão nova no GitHub e mostra um aviso com o botão Baixar.
* **Cores personalizáveis** (Opções → Cores do app): temas prontos (Azul, Grafite, Esmeralda, Violeta) ou cada
  cor individual (blocos livres/em uso/fragmentados/não movíveis, fundo, cartões, destaque e texto). Aplica na
  hora e fica salvo. Os blocos livres são cinza por padrão.
* **Posição da janela:** o programa lembra tamanho, posição e se estava maximizado (sempre dentro da tela), e a
  janela inicial se ajusta a telas pequenas.
* **Instância única:** abrir o programa de novo com ele já aberto (inclusive escondido na bandeja) mostra o
  aviso "O Unfrag Dark já está aberto" e traz a janela existente para a frente.
* **Detalhe do bloco:** clique em um quadrado do mapa para ver qual bloco é (número, clusters, posição no
  disco, % livre) e quais arquivos fragmentados têm dados nele; a lista passa a mostrar só esses arquivos,
  com quantos pedaços de cada um caem no bloco. Clique de novo no quadrado (ou em *Mostrar todos*) para voltar.
* **Bandeja do sistema:** ao clicar no X a janela some e o programa continua em execução ao lado do relógio
  (inclusive uma análise/desfragmentação em andamento). Clique no ícone para abrir; o menu do ícone tem
  *Abrir*, *Parar operação* e *Sair*. Dá para desligar em Opções ("Fechar a janela minimiza para a bandeja").

## Segurança — leia antes de usar em disco real

* **Faça backup.** O `FSCTL_MOVE_FILE` é a API oficial e segura do NTFS, mas qualquer ferramenta que
  mexe no disco pode encontrar falhas de hardware ou de energia.
* O motor (algoritmo, mapa livre, cancelamento, tratamento de falhas, invariantes de integridade) é
  coberto por **testes automatizados com um volume simulado**. O **backend Windows** (`backends/windows.py`)
  foi escrito seguindo a documentação, mas **ainda não foi validado em hardware real** pelo autor —
  comece por `analyze`, depois por uma pasta de teste (`--path`) antes de rodar no disco todo.
* Em SSD a desfragmentação é desnecessária e reduz a vida útil; o app ignora SSDs por padrão.
* Requer execução como Administrador. Todo o acesso é local; o app não faz conexões de rede e não
  coleta telemetria.

## Desenvolvimento

```bash
pip install -e . pytest
pytest
```

Estrutura: `engine.py` (algoritmo), `freespace.py` (mapa de espaço livre), `backends/windows.py`
(API do Windows), `backends/fake.py` (volume simulado), `diskmap.py`, `cli.py`, `gui.py`, `scheduler.py`.

Veja também [`docs/ANALYSIS.md`](docs/ANALYSIS.md) (análise estática do instalador que motivou a
lista de funcionalidades) e [`docs/CLEAN-ROOM.md`](docs/CLEAN-ROOM.md).

## Licença

MIT — veja [LICENSE](LICENSE).

## Velocidade do modo Espaço livre

* A busca de buracos (*first-fit*) usa dicas por classe de tamanho e pula direto para o primeiro trecho
  que pode comportar o arquivo, em vez de varrer todos os trechos livres a cada arquivo (em um teste sintético
  com 200 mil trechos livres: ~112 s → ~7 s para 60 mil buscas, com resultados idênticos).
* A compactação para assim que não resta mais nenhum buraco antes dos arquivos que faltam.
* A verificação final relê só o mapa de espaço livre e os arquivos que foram movidos, em paralelo
  (respeitando *Threads de leitura*), em vez de reanalisar o volume inteiro. Use *Reanálise completa ao final*
  (GUI) ou `--full-rescan` (CLI) para o comportamento antigo.
* A CLI atualiza o progresso no máximo ~15 vezes por segundo (escrever no console a cada arquivo era lento).

O tempo restante é dominado pela cópia real dos clusters pelo NTFS (`FSCTL_MOVE_FILE`), que depende do disco.
