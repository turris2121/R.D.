# Visión del futuro

Tablero que se actualiza solo y compara las tasas de interés de Estados Unidos y México, con el tipo de cambio y las próximas elecciones:

1. **Posible fecha de cambio:** la próxima decisión de la Fed (FOMC) y de Banxico, con el resultado más probable y la última decisión. Incluye el calendario completo de reuniones, con las decisiones pasadas marcadas como baja / se mantiene / sube.
2. **Posibilidad de cambio de tasa:** probabilidad de que cada banco baje, mantenga o suba su tasa en la próxima reunión, estimada con los rendimientos de T-bills y CETES.
3. **Tasa actual:** México vs EE. UU. a 1 mes, 3 meses, 6 meses, 1 año, 2 años (CETES vs T-bills y Treasuries), 5 y 10 años (Bono M vs Treasury).
4. **Probabilidad tipo de cambio:** el FIX de hoy y, para cada elección próxima, la probabilidad de un movimiento grande del peso en la semana de la elección, medida con la historia del FIX desde 2014. También muestra las probabilidades a 1, 3, 6 y 12 meses.

Además: historial de las tasas de referencia (gráfica de 3 años), otras tasas de corto plazo y una explicación de los cálculos.

Elecciones incluidas: intermedia de México (6 jun 2027), presidencial de EE. UU. (7 nov 2028), presidencial de México (2 jun 2030) e intermedia de EE. UU. (5 nov 2030). Las fechas están en `config.json → elections`.

Todos los días a las 13:00 UTC (07:00 en la Ciudad de México), una GitHub Action ejecuta `scripts/fetch_rates.py`, guarda los datos nuevos y vuelve a publicar el sitio en GitHub Pages. **Los lunes también envía el resumen por Telegram.**

## Fuentes de datos

| Fuente | Qué aporta | ¿Requiere clave? |
|---|---|---|
| [Fed de Nueva York](https://markets.newyorkfed.org/static/docs/markets-api.html) | rango objetivo de la Fed, EFFR, SOFR | no |
| [Tesoro de EE. UU.](https://home.treasury.gov/resource-center/data-chart-center/interest-rates) | rendimientos de T-bills y Treasuries (1 mes a 10 años) | no |
| [FRED](https://fred.stlouisfed.org) | respaldo de las dos anteriores | no |
| [federalreserve.gov](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) | calendario de reuniones del FOMC | no |
| [API SIE de Banxico](https://www.banxico.org.mx/SieAPIRest/service/v1/) | tasa objetivo, TIIE, CETES (28 a 728 días), Bono M 5 y 10 años, FIX | token gratuito |
| [Calendario de Banxico (PDF)](https://www.banxico.org.mx/publicaciones-y-prensa/anuncios-de-las-decisiones-de-politica-monetaria/anuncios-politica-monetaria-t.html) | fechas de decisión de Banxico | no |

Si una fuente falla, se usa la copia anterior guardada en `data/raw/`, y tanto la página como el mensaje de Telegram muestran un aviso.

## Configuración (una sola vez, unos 10 minutos)

1. **Token de Banxico.** Entra a <https://www.banxico.org.mx/SieAPIRest/service/v1/token>, escribe tu correo y resuelve el captcha. El token aparece en pantalla.
2. **Bot de Telegram.**
   1. En Telegram, escríbele a **@BotFather**, envía `/newbot` y sigue las instrucciones. Te dará un **token del bot**.
   2. Abre un chat con tu bot nuevo y envíale cualquier mensaje (por ejemplo, «hola»).
   3. Obtén tu **chat id**. En tu Mac ejecuta:
      ```sh
      cd ~/rates-dashboard
      TELEGRAM_BOT_TOKEN=<token del bot> python3 scripts/notify.py --chat-id
      ```
      Muestra algo como `{123456789: 'Tu nombre'}`; el número es el chat id.
3. **Crea el repositorio en GitHub** (vacío, sin README) en <https://github.com/new> y sube el código:
   ```sh
   cd ~/rates-dashboard
   git remote add origin https://github.com/<tu-usuario>/<repositorio>.git
   git push -u origin main
   ```
4. En el repositorio, abre **Settings → Secrets and variables → Actions → New repository secret** y agrega:
   - `BANXICO_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. **Settings → Pages → Source: GitHub Actions.**
6. **Actions → Update rates → Run workflow.** Marca **Enviar el resumen de Telegram ahora** para probar. El sitio queda en `https://<tu-usuario>.github.io/<repositorio>/`.

## Cómo se estima la posibilidad de cambio de tasa

Si un banco central mueve su tasa 0.25 en una reunión que está a *k* días, el promedio de la tasa a un día durante la vida de un bono de *h* días cambia 0.25 × (*h*−*k*)/*h*. Por lo tanto:

```
movimiento implícito en la reunión = (rendimiento del bono − diferencial usual − tasa de referencia) ÷ ((h − k) / h)
probabilidad de un movimiento de 0.25 = movimiento implícito ÷ 0.25   (máximo 100%)
```

- **Fed:** T-bills a 1, 3 y 6 meses contra la tasa efectiva de fondos federales.
- **Banxico:** CETES a 28, 91 y 182 días contra la tasa objetivo.
- El script usa el bono más corto cuya vida cubra al menos un tercio del tiempo después de la reunión.
- **Diferencial usual (basis):** la mediana de (rendimiento del bono − tasa de referencia que realmente estuvo vigente durante la vida del bono) en los últimos 2 años. Para fijarlo a mano, escribe un número en `config.json → probability`.

Cada lectura también lleva una etiqueta de **señal**:
- **clara:** la diferencia está fuera de la mitad central del ruido normal de ese bono, y los plazos más largos apuntan en la misma dirección.
- **mixta:** un plazo más largo no coincide o está dentro de su propio ruido. Tómala como confianza baja.
- **débil:** la diferencia está dentro del ruido normal. Léela como «se mantiene».

Es una estimación aproximada. Los rendimientos de los bonos también se mueven por oferta, impuestos y liquidez, y cuando la reunión cae al final de la vida de un bono, los movimientos pequeños se amplifican. El FedWatch de CME usa futuros de fondos federales y es más preciso, pero CME no permite el acceso automatizado. Úsalo para comparar.

## Cómo se estima la probabilidad del tipo de cambio

**Por elección.** Con el FIX desde 2014 se mide cuánto se movió el peso entre 5 días antes y 10 días después de cada elección pasada (México: 2015, 2018, 2021, 2024; EE. UU.: 2016, 2018, 2020, 2022, 2024) y se compara con cualquier periodo normal de 15 días. Con esa variación se calcula, para cada elección próxima:

- la probabilidad de un movimiento de 5% o más en la semana de la elección (frente a una semana normal);
- la probabilidad de que el dólar suba o baje 3% o más;
- el tipo de cambio que descuenta el mercado para esa fecha (forward) y su rango probable al 80%.

Son pocas elecciones, así que es una guía, no un pronóstico. Las intermedias y las presidenciales usan las mismas cifras por país, porque no hay suficiente historia para separarlas. Las fechas de elecciones pasadas están en `config.json → elections_history`.

**Por plazo (1 a 12 meses).**

```
forward = FIX × (1 + CETES × días/360) ÷ (1 + T-bill × días/360)
ln(futuro / hoy) ~ Normal(ln(forward / hoy) − σ²t/2, σ²t)
```

σ es la volatilidad anual observada del FIX en los últimos 252 días hábiles y t es el plazo en años. El forward es lo que descuenta el mercado, no un pronóstico. Por el *carry* (la diferencia de tasas), históricamente el peso suele quedar mejor que su forward, así que la probabilidad de un peso más débil puede estar sobrestimada. La volatilidad implícita de opciones daría un rango mejor, pero no hay una fuente gratuita.

## Ejecutar en tu computadora

```sh
pip3 install -r requirements.txt
BANXICO_TOKEN=<token> python3 scripts/fetch_rates.py
python3 scripts/notify.py --dry-run          # muestra el mensaje de Telegram sin enviarlo
python3 -m http.server -d site 8000          # luego abre http://localhost:8000
```

## Notas

- Banxico publica el calendario del año siguiente alrededor de diciembre y el script lo toma automáticamente. Si alguna vez no se puede leer, agrega las fechas en `config.json → banxico_calendar_manual`.
- La página muestra un aviso si el título de una serie de Banxico no coincide con lo esperado. Eso significa que hay que corregir el ID en `config.json → banxico_series`.
- GitHub desactiva los workflows programados en repositorios sin actividad durante 60 días. Los commits diarios de datos normalmente cuentan como actividad.
