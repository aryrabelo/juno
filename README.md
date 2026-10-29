# Juno

Comandos de voz sem as mãos para macOS, com reconhecimento **offline** ([Vosk](https://alphacephei.com/vosk/)).
Feito para ditar mensagens com o [FluidVoice](https://github.com/altic-dev/FluidVoice) e enviar sem tocar no teclado.

| Você diz | O Juno faz |
|---|---|
| **“juno”** | Aperta ⌥⌘. para o FluidVoice começar a ditar |
| **“câmbio”** (no fim da fala, seguido de silêncio) | Para o ditado, espera o texto ser colado e aperta Enter |
| **“agente &lt;apelido&gt;”** (frase sozinha) | Abre a busca do app (⌘K), digita o nome do agente e aperta Enter |

O áudio nunca sai do computador: o Juno só reconhece as palavras de comando, com um modelo local.

## Requisitos
- macOS, Python 3.9+
- FluidVoice com o atalho de ditado **⌥⌘.** e o *Spoken Send* com a frase **“câmbio”** (ele tira a palavra do texto)

## Instalação
```bash
git clone https://github.com/aryrabelo/juno.git
cd juno
./install.sh
.venv/bin/python juno.py --test   # só mostra o que ouviu, sem apertar teclas
.venv/bin/python juno.py          # modo real
```
Na primeira tecla apertada, o macOS pede permissão de **Acessibilidade** e de **Microfone** para o terminal ou o Python. Aprove.

## Troca de agente por voz
Edite `agentes.tsv` (criado a partir de `agentes.example.tsv`): uma linha por agente, `apelido<TAB>texto da busca`.
O apelido precisa ser uma palavra do vocabulário do modelo Vosk em português.

## Limitações conhecidas
- Precisa falar alto e claro; a palavra é confirmada depois de uma pausa (≈1–3 s).
- O estado do ditado é lido do log do FluidVoice (`~/Library/Logs/Fluid/Fluid.log`).
- Não inicia sozinho no login (ainda).

## Próximo passo
- **“agente próximo”**: ir direto para o próximo agente que precisa de atenção, ou seja, o que tem o
  número de mensagens não lidas ao lado do nome na barra lateral. Hoje o app não tem atalho para isso;
  o caminho é ler esse contador na barra lateral (Acessibilidade) ou pedir o atalho ao app.

## Licença
MIT
