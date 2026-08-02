//+------------------------------------------------------------------+
//|                                                AI_Scalper_Pro.mq5|
//| v9.0 — по факту реальных сделок пользователя (TP +$1 против SL    |
//| -$30...-$111+, счёт в минусе) + просьба "сделай то же самое для   |
//| советника" (уже сделано в Python-программе):                      |
//|   1) КРИТИЧНО: MinRiskRewardRatio — жёсткий пол, TP никогда не    |
//|      меньше SL x3 (см. RiskManager.mqh ApplyMinRiskRewardFloor).  |
//|      RiskRewardRatio поднят с 1.2 до 3.0.                          |
//|   2) UseMaxProfitRide — без фикс. TP, тянуть максимум прибыли      |
//|      только BE/трейлингом/Profit Lock (верхней границы нет).      |
//|   3) Собственная стратегия советника (CustomStrategy.mqh) —       |
//|      второе независимое мнение (momentum/ускорение/               |
//|      согласованность/расширение диапазона), портировано 1:1 из     |
//|      custom_strategy.py (Python-программа).                       |
//|   4) UseAdaptiveScoreWeights — "умнее" score: веса компонентов      |
//|      подстраиваются под режим рынка (см. AdaptiveMultiplier в     |
//|      SignalEngine.mqh), вместо фиксированных.                     |
//|   5) Кнопки на графике "Закрыть прибыльные"/"Закрыть убыточные"    |
//|      (см. Dashboard.mqh + OnChartEvent) — закрывают позиции ЭТОГО  |
//|      EA (по _Symbol+MagicNumber) по текущему плавающему профиту.  |
//| v8.0 — (п.24) ТРИ крупных дополнения:                             |
//|   1) Режим торговли на выбор: СКАЛЬПИНГ / НОВОСТИ / ОБА (TradingMode|
//|      в Config.mqh). Новостной режим ловит пробой волатильности    |
//|      сразу после HIGH-импакт события, а не избегает его.          |
//|   2) Автонастройка под ЛЮБУЮ пару рынка (AutoAdaptToSymbol):       |
//|      пороги в "пунктах" (откат, TP min/max, трейлинг, Profit Lock)|
//|      теперь авто-масштабируются под ATR конкретного инструмента —  |
//|      золото/форекс/крипта больше не требуют ручной перенастройки. |
//|   3) Вес внешнего AI-сигнала авто-адаптируется под режим рынка:    |
//|      больше веса во флэте (свой паттерн ненадёжен), меньше в      |
//|      подтверждённом тренде (структура и так надёжна).             |
//|   v7.0 — внешний AI-сигнал включён по умолчанию (п.23): рассчитан |
//|   на локальный мост bridge_ai_market_analysis.py (Claude/ChatGPT). |
//|   v6.9 — плавное снижение риска по серии убытков + защита от      |
//|   "ролловерной дыры" (п.22).                                       |
//|   v6.8 — исправлен перекос риск:прибыль в "Агрессивном" (п.21).  |
//|   v6.7 — анти-дребезг (п.20): бот не разворачивается против       |
//|   недавно закрытой сделки минимум MinBarsBetweenReversal баров.   |
//|   Config.mqh / Indicators.mqh / NewsAI.mqh / MarketRegime.mqh /  |
//|   MarketContext.mqh / SignalEngine.mqh / RiskManager.mqh /       |
//|   TradeManager.mqh / Dashboard.mqh                                |
//| Этот файл — только "дирижёр": подключает модули и определяет     |
//| стандартные обработчики событий MT5 (OnInit/OnTick/...).         |
//| ВАЖНО: все .mqh должны лежать в ТОЙ ЖЕ папке, что и этот .mq5.    |
//+------------------------------------------------------------------+
#property copyright "AI Scalper Pro"
#property version   "9.00"
#property strict

#include <Trade/Trade.mqh>
CTrade trade;

#include "Config.mqh"
#include "Indicators.mqh"
#include "NewsAI.mqh"
#include "MarketRegime.mqh"
#include "MarketContext.mqh"
#include "SignalEngine.mqh"
#include "CustomStrategy.mqh"
#include "MultiIndicator.mqh"
#include "RiskManager.mqh"
#include "TradeManager.mqh"
#include "Dashboard.mqh"

//===================== ИНИЦИАЛИЗАЦИЯ =================
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFillingBySymbol(_Symbol); // п.16: автоопределение filling mode — частая причина "тихих" отказов ордеров

   ApplyRiskProfile();

   if(!CreateIndicators())
   {
      Print("Ошибка создания индикаторов");
      return(INIT_FAILED);
   }
   CreateMarketContext(); // п.19: необязательный контекст, ошибка тут не валит EA
   CreateMultiIndicatorHandles(); // MACD/Bollinger/Stochastic, необязательно — ошибка тут не валит EA

   // п.25: дневное состояние восстанавливается из глобальных переменных
   // терминала. Раньше оно обнулялось при каждом OnInit — то есть при смене
   // таймфрейма или правке любого параметра дневной лимит убытка начинался
   // заново. См. LoadDailyState() в RiskManager.mqh.
   LoadDailyState();

   EnsureCSVHeader();
   CreateDashboardButtons(); // "Закрыть прибыльные"/"Закрыть убыточные" (см. Dashboard.mqh)

   if(UseCustomStrategy)
      Print("Собственная стратегия советника включена (CustomStrategy.mqh v", CUSTOM_STRATEGY_VERSION, "), вес ", CustomStrategyWeight);

   if(UseExternalSignal)
      Print("Внешний AI-сигнал включён. URL: ", ExternalSignalURL,
            " — добавь его в Tools -> Options -> Expert Advisors -> Allow WebRequest for listed URL, иначе запросы будут падать с ошибкой 4060.");

   Print("AI Scalper Pro v8.0 запущен на ", _Symbol, " ", EnumToString(_Period),
         " | тренд с ", EnumToString(TrendTimeframe), " | профиль: ", g_activeProfileName,
         " | режим торговли: ", EnumToString(TradingMode));
   return(INIT_SUCCEEDED);
}

//===================== ДЕИНИЦИАЛИЗАЦИЯ ===============
void OnDeinit(const int reason)
{
   ReleaseIndicators();
   ReleaseMarketContext();
   ReleaseMultiIndicatorHandles();
   RemoveDashboardButtons();
   Comment("");
   Print("AI Scalper Pro остановлен");
}

//===================== КЛИКИ ПО КНОПКАМ ДАШБОРДА ========
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id==CHARTEVENT_OBJECT_CLICK)
      HandleDashboardButtonClick(sparam);
}

//===================== НОВАЯ СВЕЧА / ДЕНЬ =============
bool IsNewBar()
{
   datetime current=iTime(_Symbol,_Period,0);
   if(current!=LastBar) { LastBar=current; g_barCounter++; return true; }
   return false;
}
void CheckNewDay()
{
   MqlDateTime tmNow, tmLast;
   TimeToStruct(TimeCurrent(), tmNow);
   TimeToStruct(LastTradeDay, tmLast);
   if(tmNow.day!=tmLast.day || tmNow.mon!=tmLast.mon || tmNow.year!=tmLast.year)
      StartNewDayState(); // п.25: сохраняет новое состояние в глобальных переменных
}

//===================== ОСНОВНОЙ ЦИКЛ ====================
void OnTick()
{
   CheckNewDay();
   ManageOpenPositions();
   UpdateDashboard();

   if(!IsNewBar()) return;
   CleanupPartialClosedTickets();
   UpdateMarketRegime(); // п.18: считаем режим рынка раз в бар, до расчёта score
   if(!TradingAllowed()) { g_lastRejectReason="Торговля приостановлена (лимит/просадка/пауза)"; return; }
   if(CountOpenPositions()>=g_effMaxOpenPositions) { g_lastRejectReason="Достигнут лимит одновременных сделок"; return; }
   if(TradesToday>=g_effMaxTradesPerDay) { g_lastRejectReason="Достигнут лимит сделок за день"; return; }
   if(!SpreadOK())      { g_lastRejectReason="Спред слишком широкий"; return; }
   if(!TimeFilterOk())  { g_lastRejectReason="Вне торгового времени"; return; }
   if(GetATRValue()<=0) { g_lastRejectReason="Индикаторы не готовы"; return; }
   if(!RolloverGuardOk()) { g_lastRejectReason="Ролловерная дыра ликвидности — сигнал пропущен"; return; }

   int direction=0;
   double score=0;
   bool isNewsEntry=false;
   bool hedgeBothDirections=false; // true = вместо одной стороны открываем BUY и SELL сразу (см. ниже)
   // Объявлены здесь (а не внутри if(direction==0) ниже), т.к. нужны в хедж-режиме
   // и при отправке ордеров дальше по функции — в MQL5 блочная область видимости,
   // внутри if{} они были бы не видны за его пределами.
   double buyScore=0, sellScore=0;

   // п.24: НОВОСТНОЙ режим (или ОБА) — пробуем поймать пробой на свежей HIGH-новости
   // ПЕРЕД обычным скорингом. Здесь намеренно НЕ проверяются NewsFilterOk/VolatilityOk —
   // это ровно те события/скачки, которые этот режим и должен ловить.
   if(TradingMode==MODE_NEWS_TRADING || TradingMode==MODE_BOTH)
   {
      int newsDir=0; double newsConf=0;
      if(DetectNewsBreakout(newsDir,newsConf))
      {
         direction=newsDir;
         score=newsConf;
         isNewsEntry=true;
         g_lastBuyScore  = (direction==1)  ? score : 0;
         g_lastSellScore = (direction==-1) ? score : 0;
      }
      else if(TradingMode==MODE_NEWS_TRADING)
      {
         g_lastRejectReason="Новостной режим: свежего пробоя после HIGH-новости нет";
         return;
      }
   }

   // Обычный скальпинг-паттерн — если новостной вход не сработал (или режим = только скальпинг)
   if(direction==0)
   {
      if(!NewsFilterOk())  { g_lastRejectReason="Рядом важная новость"; return; }
      if(!VolatilityOk())  { g_lastRejectReason="Резкий скачок волатильности — сигнал пропущен"; return; }

      // Анти-"зеркало" фильтр #2: во ФЛЭТЕ трендовый паттерн откат+пробой чаще
      // всего ложный — раньше это только штрафовало score, вход всё равно был
      // возможен. Если ВКЛ — вход блокируется ПОЛНОСТЬЮ, пока режим не
      // сменится на тренд/неопределённый. Действует всегда, включая профили
      // с ignore_soft_filters ("Истеричка") — именно там проблема была найдена.
      if(BlockEntryInRange && g_currentRegime==REGIME_RANGE)
      {
         g_lastRejectReason="Флэт: вход заблокирован (анти-разворотный фильтр)";
         return;
      }

      buyScore  = CalcSignalScore(1);
      sellScore = CalcSignalScore(-1);

      if(UseExternalSignal)
      {
         FetchExternalSignal();
         buyScore  = ApplyExternalSignal(1, buyScore);
         sellScore = ApplyExternalSignal(-1, sellScore);
      }

      // Собственная стратегия советника (CustomStrategy.mqh, по просьбе
      // пользователя — то же самое, что уже сделано в Python-программе) —
      // второе, независимое мнение, подмешивается с ограниченным весом,
      // как внешний AI-сигнал выше.
      if(UseCustomStrategy)
      {
         double customBuy  = CalcCustomScore(1);
         double customSell = CalcCustomScore(-1);
         g_lastCustomScore = MathMax(customBuy, customSell);
         buyScore  = ApplyCustomStrategy(buyScore, customBuy);
         sellScore = ApplyCustomStrategy(sellScore, customSell);
      }

      // Доп. подтверждение классическими индикаторами (MultiIndicator.mqh:
      // MACD/Bollinger/Stochastic) — по просьбе пользователя "используй как
      // можно больше индикаторов и стратегий", то же ограниченное подмешивание.
      if(UseMultiIndicator)
      {
         double miBuy  = CalcMultiIndicatorScore(1);
         double miSell = CalcMultiIndicatorScore(-1);
         g_lastMultiIndicatorScore = MathMax(miBuy, miSell);
         buyScore  = ApplyMultiIndicator(buyScore, miBuy);
         sellScore = ApplyMultiIndicator(sellScore, miSell);
      }

      g_lastBuyScore=buyScore; g_lastSellScore=sellScore;

      if(UseScoreFilter)
      {
         bool buyOk  = buyScore>=g_effMinScoreToTrade;
         bool sellOk = sellScore>=g_effMinScoreToTrade;
         // Хедж-режим (сейчас только у профиля "Истеричка"): как только хотя бы
         // одна сторона проходит порог — открываем ОБЕ стороны сразу, вместо
         // выбора одной по большему score. Дальше у каждой ноги совершенно
         // обычный SL/TP/BE/трейлинг/Profit Lock (см. ManageOpenPositions) —
         // убыточная нога ограничена своим стоп-лоссом, прибыльная закрывается
         // как обычно.
         if(g_effHedgeBothDirections && (buyOk || sellOk))
         {
            hedgeBothDirections=true;
            score=MathMax(buyScore,sellScore);
         }
         else if(buyOk && buyScore>=sellScore) { direction=1;  score=buyScore; }
         else if(sellOk && sellScore>buyScore) { direction=-1; score=sellScore; }
         else
         {
            g_lastRejectReason = StringFormat("Score BUY=%.1f SELL=%.1f < %.1f", buyScore, sellScore, g_effMinScoreToTrade);
            return;
         }
      }
      else
      {
         // Без порога score — вход по жёстким условиям паттерна (структура + price action)
         bool buyPattern  = PullbackBreakoutOk(1)  && EMAStackOk(1)  && IsBullishConfirmation(SIGNAL_SHIFT);
         bool sellPattern = PullbackBreakoutOk(-1) && EMAStackOk(-1) && IsBearishConfirmation(SIGNAL_SHIFT);
         if(buyPattern)       { direction=1;  score=buyScore; }
         else if(sellPattern) { direction=-1; score=sellScore; }
         else
         {
            g_lastRejectReason = "Паттерн Pullback+PA не найден";
            return;
         }
      }
   }

   // п.20: анти-дребезг — не разворачиваемся против недавно закрытой сделки,
   // рынку нужно хотя бы пару баров показать новое направление (действует и в новостном режиме).
   // В хедж-режиме НЕ проверяется — мы намеренно открываем обе стороны сразу.
   if(!hedgeBothDirections && !ReversalCooldownOk(direction))
   {
      g_lastRejectReason = StringFormat("Анти-дребезг: жду %d бар(а) после сделки в другую сторону", MinBarsBetweenReversal);
      return;
   }

   int legsToOpen = hedgeBothDirections ? 2 : 1;

   // Хедж открывает 2 позиции за раз — нужно, чтобы оба слота были свободны,
   // иначе получится однобокая "хеджированная" сделка, которая хедж не даёт.
   if(hedgeBothDirections && (CountOpenPositions()+legsToOpen > g_effMaxOpenPositions))
   {
      g_lastRejectReason = "Хедж (обе стороны): не хватает свободных слотов одновременных сделок";
      return;
   }

   double atr=GetATRValue(SIGNAL_SHIFT); // ATR закрытой сигнальной свечи, а не формирующейся (п.3)
   // п.24: новостной вход — стоп шире, волатильность в момент выхода данных объективно выше
   double slDist=atr*g_effATRSLMultiplier*(isNewsEntry?NewsVolatilitySLBoost:1.0);
   double lot=CalcLot(slDist);
   // п.25: CalcLot возвращает 0, когда даже минимальный лот брокера рискует
   // больше заданного процента — раньше в этом случае сделка молча открывалась
   // завышенным объёмом. Причина отказа уже записана внутри CalcLot.
   if(lot<=0) return;
   double tpDist;
   if(UseMaxProfitRide)
   {
      // "Тянуть максимальную прибыль сколько возможно" (просьба пользователя):
      // без фиксированного TP, сделку от сих пор ведёт ТОЛЬКО BE/ATR-трейлинг/
      // Profit Lock (см. ManageOpenPositions в TradeManager.mqh) — закрытие
      // происходит, когда цена разворачивается и выбивает трейлинг-стоп.
      tpDist=0.0;
   }
   else if(g_effUseMoneyTP)
   {
      // slDist передаём, чтобы CalcTPDistanceMoney гарантированно поднял TP
      // минимум до slDist*MinRiskRewardRatio, если денежная цель профиля
      // (TargetProfitMoney) окажется слишком скромной относительно риска.
      tpDist=CalcTPDistanceMoney(lot, g_effTargetProfitMoney, slDist);
   }
   else
   {
      tpDist=CalcTPDistance(slDist);
   }

   // п.16: издержки против TP — если спред съедает слишком большую часть цели прибыли, смысла нет
   if(!SpreadCostOk(lot,tpDist)) { g_lastRejectReason="Спред съедает слишком большую часть TP"; return; }

   // п.16: совокупный риск по ВСЕМ открытым сделкам этого EA не должен превышать потолок,
   // даже если MaxOpenPositions разрешает несколько сделок сразу на одном инструменте
   // п.25: риск новой сделки считаем точной суммой от терминала (OrderCalcProfit
   // внутри MoneyRiskPerLot) — на золоте и кроссах tick value давал неверный риск
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double newTradeRiskPct=0;
   if(equity>0)
   {
      double riskMoneyPerLot=MoneyRiskPerLot(slDist);
      if(riskMoneyPerLot>0)
         newTradeRiskPct=riskMoneyPerLot*lot/equity*100.0;
   }
   // В хедже считаем риск по ОБЕИМ ногам (консервативно — реальный чистый риск
   // обычно меньше, т.к. ноги в противоположных направлениях, но так надёжнее).
   if(GetOpenRiskPercent()+newTradeRiskPct*legsToOpen > g_effMaxTotalRiskPercent)
   {
      g_lastRejectReason="Превышен общий риск по открытым позициям";
      return;
   }

   if(!hedgeBothDirections)
   {
      ExecuteMarketOrder(direction, lot, slDist, tpDist, score, atr/_Point);
   }
   else
   {
      // Каждая нога — обычный, полностью независимый рыночный ордер со своим
      // SL/TP; дальше ManageOpenPositions() ведёт обе ровно так же, как любую
      // другую сделку (Break Even/трейлинг/Profit Lock/частичное закрытие).
      bool okBuy  = ExecuteMarketOrder(1, lot, slDist, tpDist, buyScore, atr/_Point);
      bool okSell = ExecuteMarketOrder(-1, lot, slDist, tpDist, sellScore, atr/_Point);
      if(!okBuy && !okSell)
         g_lastRejectReason = "Хедж: не удалось отправить ни одну из ног";
      else if(!okBuy || !okSell)
         g_lastRejectReason = "OK (частично, хедж — одна из ног не отправилась)";
   }
}

//===================== ЗАКРЫТЫЕ СДЕЛКИ: СТАТИСТИКА + CSV =
void OnTradeTransaction(const MqlTradeTransaction &trans,
                         const MqlTradeRequest &request,
                         const MqlTradeResult &result)
{
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD) return;
   if(!HistoryDealSelect(trans.deal)) return;

   long magic=HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
   if(magic!=(long)MagicNumber) return;

   long entry=HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY) return;

   double profit=HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
   double price =HistoryDealGetDouble(trans.deal, DEAL_PRICE);
   double volume=HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
   long   dtype =HistoryDealGetInteger(trans.deal, DEAL_TYPE);
   string dir=(dtype==DEAL_TYPE_SELL)?"CLOSE_BUY":"CLOSE_SELL";

   LogTradeCSV("CLOSE", dir, price, 0, 0, volume, 0, profit);

   // п.20: анти-дребезг — запоминаем направление и бар закрытия для ReversalCooldownOk
   g_lastCloseDirection = (dtype==DEAL_TYPE_SELL) ? 1 : -1; // закрывающая sell-сделка = была BUY-позиция, и наоборот
   g_lastCloseBarIndex  = g_barCounter;

   // Статистика для дашборда (п.7)
   g_totalTrades++;
   if(profit>=0) { g_winTrades++; g_grossProfit+=profit; g_lastTradeResult="ПРИБЫЛЬ +"+DoubleToString(profit,2); }
   else          { g_grossLoss+=profit; g_lastTradeResult="УБЫТОК "+DoubleToString(profit,2); }

   // Контроль серии убытков (п.8)
   if(profit<0)
   {
      g_consecutiveLosses++;
      if(g_consecutiveLosses>=MaxConsecutiveLosses)
      {
         g_pauseUntil=TimeCurrent()+PauseHoursAfterLossStreak*3600;
         Print("Серия из ", g_consecutiveLosses, " убытков подряд. Пауза до ", TimeToString(g_pauseUntil,TIME_DATE|TIME_MINUTES));
         g_consecutiveLosses=0;
      }
   }
   else g_consecutiveLosses=0;

   // п.25: серия убытков и пауза сохраняются, чтобы пережить перезапуск
   // советника (смена таймфрейма, правка параметров, перезапуск терминала)
   SaveRiskStreakState();
}
//+------------------------------------------------------------------+
