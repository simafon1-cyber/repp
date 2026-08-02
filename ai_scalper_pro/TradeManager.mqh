//+------------------------------------------------------------------+
//| TradeManager.mqh                                                 |
//| Всё, что происходит с УЖЕ ОТКРЫТОЙ сделкой: Break Even, ATR-     |
//| трейлинг, Profit Lock (тянет SL к пиковой прибыли), частичное    |
//| закрытие, CSV-лог сделок. Требует Config.mqh и Indicators.mqh.   |
//+------------------------------------------------------------------+

//===================== CSV ЛОГ ==========================
void EnsureCSVHeader()
{
   if(!EnableCSVLog) return;
   if(!FileIsExist(CSVFileName, FILE_COMMON))
   {
      int h=FileOpen(CSVFileName, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ';');
      if(h!=INVALID_HANDLE)
      {
         FileWrite(h,"Time","Event","Direction","Price","SL","TP","Lot","Score","Profit",
                    "ATR","ADX","RSI","Spread","Symbol","Timeframe");
         FileClose(h);
      }
   }
}
void LogTradeCSV(string evt,string direction,double price,double sl,double tp,double lot,double score,double profit=0)
{
   if(!EnableCSVLog) return;
   int h=FileOpen(CSVFileName, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ';');
   if(h==INVALID_HANDLE) return;
   FileSeek(h,0,SEEK_END);
   FileWrite(h, TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS), evt, direction,
              DoubleToString(price,_Digits), DoubleToString(sl,_Digits), DoubleToString(tp,_Digits),
              DoubleToString(lot,2), DoubleToString(score,1), DoubleToString(profit,2),
              DoubleToString(GetATRValue()/_Point,1), DoubleToString(GetADXValue(),1), DoubleToString(GetRSIValue(),1),
              (string)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD), _Symbol, EnumToString(_Period));
   FileClose(h);
}

//===================== ЧАСТИЧНОЕ ЗАКРЫТИЕ: УЧЁТ =========
bool IsPartialClosed(ulong ticket)
{
   for(int i=0;i<ArraySize(g_partialClosedTickets);i++) if(g_partialClosedTickets[i]==ticket) return true;
   return false;
}
void MarkPartialClosed(ulong ticket)
{
   int n=ArraySize(g_partialClosedTickets); ArrayResize(g_partialClosedTickets,n+1); g_partialClosedTickets[n]=ticket;
}
// Тикеты закрытых позиций иначе копятся в g_partialClosedTickets вечно.
void CleanupPartialClosedTickets()
{
   for(int i=ArraySize(g_partialClosedTickets)-1;i>=0;i--)
      if(!PositionSelectByTicket(g_partialClosedTickets[i]))
         ArrayRemove(g_partialClosedTickets,i,1);
}

//===================== БЫСТРАЯ ФИКСАЦИЯ: УЧЁТ СТУПЕНЕЙ (п.25) ==========
// Отдельный учёт от IsPartialClosed выше: там один флаг "уже закрывали",
// а здесь нужно помнить НОМЕР ступени, чтобы вторая сработала после первой.
int GetQuickStage(ulong ticket)
{
   for(int i=0;i<ArraySize(g_quickStageTickets);i++)
      if(g_quickStageTickets[i]==ticket) return g_quickStageValues[i];
   return 0;
}
void SetQuickStage(ulong ticket,int stage)
{
   for(int i=0;i<ArraySize(g_quickStageTickets);i++)
      if(g_quickStageTickets[i]==ticket) { g_quickStageValues[i]=stage; return; }
   int n=ArraySize(g_quickStageTickets);
   ArrayResize(g_quickStageTickets,n+1);
   ArrayResize(g_quickStageValues,n+1);
   g_quickStageTickets[n]=ticket;
   g_quickStageValues[n]=stage;
}
// Иначе тикеты закрытых сделок копились бы в памяти бесконечно
void CleanupQuickStages()
{
   for(int i=ArraySize(g_quickStageTickets)-1;i>=0;i--)
      if(!PositionSelectByTicket(g_quickStageTickets[i]))
      {
         ArrayRemove(g_quickStageTickets,i,1);
         ArrayRemove(g_quickStageValues,i,1);
      }
}

// Закрывает заданный % от текущего объёма позиции с учётом шага и мин. лота.
// Возвращает true, если частичное закрытие реально произошло.
bool CloseVolumePercent(ulong ticket,double volume,double percent)
{
   if(percent<=0 || volume<=0) return false;
   double minLot =SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double lotStep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double closeVolume=FloorVolumeToStep(volume*percent/100.0, lotStep);
   // Закрывать меньше минимального лота нельзя; остаток тоже должен быть
   // не меньше минимального, иначе брокер отклонит запрос.
   if(closeVolume<minLot) return false;
   if(volume-closeVolume<minLot) return false;
   return trade.PositionClosePartial(ticket,closeVolume);
}

//===================== PROFIT LOCK: ПИКОВАЯ ПРИБЫЛЬ ПО ПОЗИЦИИ (п.12) ===
int FindPeakIndex(ulong ticket)
{
   for(int i=0;i<ArraySize(g_posTickets);i++)
      if(g_posTickets[i]==ticket) return i;
   return -1;
}
// Обновляет и возвращает пиковую прибыль (в пунктах) по тикету
double UpdatePeakProfit(ulong ticket,double profitPoints)
{
   int idx=FindPeakIndex(ticket);
   if(idx<0)
   {
      int n=ArraySize(g_posTickets);
      ArrayResize(g_posTickets,n+1);
      ArrayResize(g_posPeakPoints,n+1);
      ArrayResize(g_posRiskPoints,n+1);
      g_posTickets[n]=ticket;
      g_posPeakPoints[n]=profitPoints;
      g_posRiskPoints[n]=0; // заполнится в UpdatePositionRisk() тем же тикетом чуть ниже
      return profitPoints;
   }
   if(profitPoints>g_posPeakPoints[idx]) g_posPeakPoints[idx]=profitPoints;
   return g_posPeakPoints[idx];
}
// Убирает из памяти тикеты закрытых позиций
void CleanupPeakProfit()
{
   for(int i=ArraySize(g_posTickets)-1;i>=0;i--)
      if(!PositionSelectByTicket(g_posTickets[i]))
      {
         ArrayRemove(g_posTickets,i,1);
         ArrayRemove(g_posPeakPoints,i,1);
         if(i<ArraySize(g_posRiskPoints)) ArrayRemove(g_posRiskPoints,i,1);
      }
}

// Запоминает изначальный риск сделки (openPrice<->SL) ОДИН РАЗ, при первом
// же взгляде на позицию — до того как BE/трейлинг/Profit Lock успеют
// подтянуть SL ближе к цене. Это "1R" сделки. См. ProfitLockStartRFraction.
double UpdatePositionRisk(ulong ticket,double openPrice,double sl)
{
   int idx=FindPeakIndex(ticket); // тот же индекс, что и у пиковой прибыли (параллельные массивы)
   if(idx<0 || idx>=ArraySize(g_posRiskPoints)) return 0;
   // g_posRiskPoints растёт синхронно с g_posTickets/g_posPeakPoints ниже
   // (см. UpdatePeakProfit) — если размер ещё не совпал, просто пропускаем.
   if(g_posRiskPoints[idx]==0 && sl!=0)
      g_posRiskPoints[idx]=MathAbs(openPrice-sl)/_Point;
   return g_posRiskPoints[idx];
}
// Выбирает более защищающий прибыль SL: для buy — выше, для sell — ниже. 0 = стопа ещё нет.
double BetterSL(long type,double a,double b)
{
   if(a==0) return b;
   if(b==0) return a;
   return (type==POSITION_TYPE_BUY) ? MathMax(a,b) : MathMin(a,b);
}
// Для дашборда: текущая и пиковая прибыль по нашей (единственной приоритетной) открытой позиции
bool GetOwnPositionProfitInfo(double &curPts,double &peakPts)
{
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=(long)MagicNumber) continue;

      long type=PositionGetInteger(POSITION_TYPE);
      double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
      double price=(type==POSITION_TYPE_BUY)?SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      curPts=(type==POSITION_TYPE_BUY)?(price-openPrice)/_Point:(openPrice-price)/_Point;
      int idx=FindPeakIndex(ticket);
      peakPts=(idx>=0)?g_posPeakPoints[idx]:curPts;
      return true;
   }
   return false;
}

//===================== ОТПРАВКА ОРДЕРА С ПОВТОРОМ (п.16) ================
// По опыту MQL5-форумов: реквот/сдвиг цены — частая и "временная" ошибка при
// скальпинге, её стоит просто повторить с СВЕЖЕЙ ценой, а не терять сигнал.
// На прочих ошибках (нет денег, невалидный объём и т.п.) повтор бессмысленен.
bool ExecuteMarketOrder(int direction,double lot,double slDist,double tpDist,double score,double atrPts)
{
   string dirTxt=(direction==1)?"BUY":"SELL";

   for(int attempt=1; attempt<=OrderRetryAttempts; attempt++)
   {
      double price=(direction==1)?SymbolInfoDouble(_Symbol,SYMBOL_ASK):SymbolInfoDouble(_Symbol,SYMBOL_BID);
      double sl=(direction==1)?price-slDist:price+slDist;
      // tpDist<=0 значит "TP не выставляется вообще" (см. UseMaxProfitRide —
      // тянуть максимум прибыли только BE/трейлингом/Profit Lock). Раньше здесь
      // по ошибке считалось tp=price ("TP прямо на входе") — сделка закрывалась
      // бы почти сразу; правильный для MT5 способ "нет TP" — это tp=0.0.
      double tp=(tpDist<=0) ? 0.0 : ((direction==1)?price+tpDist:price-tpDist);

      if(!CheckStopsDistance(price,sl,tp))
      {
         Print("SL/TP слишком близко, ордер отменён");
         return false;
      }

      bool sent=(direction==1) ? trade.Buy(lot,_Symbol,price,sl,tp) : trade.Sell(lot,_Symbol,price,sl,tp);
      if(sent)
      {
         TradesToday++;
         g_lastRejectReason="OK";
         PrintFormat("%s | Score %.1f | ATR %.1f | Spread %d | Попытка %d",
                     dirTxt, score, atrPts, (long)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD), attempt);
         LogTradeCSV("OPEN",dirTxt,price,sl,tp,lot,score);
         return true;
      }

      uint retcode=trade.ResultRetcode();
      Print("Ошибка ", dirTxt, ": ", retcode, " ", trade.ResultRetcodeDescription());

      bool transient = (retcode==TRADE_RETCODE_REQUOTE || retcode==TRADE_RETCODE_PRICE_CHANGED || retcode==TRADE_RETCODE_PRICE_OFF);
      if(!transient) return false; // остальные ошибки повтором не лечатся
   }
   return false;
}

//===================== ЗАКРЫТЬ ПРИБЫЛЬНЫЕ / УБЫТОЧНЫЕ (кнопки на графике) ===
// По просьбе пользователя ("сделай то же самое для советника" — уже было
// сделано в Python-программе). Кнопки создаются в Dashboard.mqh, клик
// обрабатывается в OnChartEvent (AI_Scalper_Pro.mq5). В отличие от Python-версии
// (которая закрывает ВСЕ позиции счёта, включая открытые вручную на любых
// инструментах через общий дашборд) — советник видит и закрывает только СВОИ
// позиции на СВОЁМ графике (этот _Symbol + этот MagicNumber), т.к. у EA нет
// отдельного окна подтверждения и он не должен трогать чужие сделки/инструменты.
void CloseProfitablePositions()
{
   int closedCount=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=(long)MagicNumber) continue;
      if(PositionGetDouble(POSITION_PROFIT)<0) continue;
      if(trade.PositionClose(ticket)) closedCount++;
   }
   PrintFormat("Кнопка 'Закрыть прибыльные': закрыто позиций: %d", closedCount);
}
void CloseLosingPositions()
{
   int closedCount=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=(long)MagicNumber) continue;
      if(PositionGetDouble(POSITION_PROFIT)>=0) continue;
      if(trade.PositionClose(ticket)) closedCount++;
   }
   PrintFormat("Кнопка 'Закрыть убыточные': закрыто позиций: %d", closedCount);
}

// Ступенчатая фиксация: чем выше пик прибыли (в единицах эффективного порога
// ProfitLockStartPoints, авто-масштабированного под ATR — это и есть unit),
// тем больший % пика запирается стопом. Возвращает % САМОГО СТАРШЕГО тира,
// до которого дорос пик; если тиры выключены/unit некорректен — падает
// обратно на плоский ProfitLockPercent.
double TieredLockPercent(double peakPoints,double unit)
{
   if(!UseTieredProfitLock || unit<=0) return ProfitLockPercent;
   double best=ProfitLockPercent;
   if(peakPoints>=ProfitLockTier1Mult*unit) best=ProfitLockTier1Pct;
   if(peakPoints>=ProfitLockTier2Mult*unit) best=ProfitLockTier2Pct;
   if(peakPoints>=ProfitLockTier3Mult*unit) best=ProfitLockTier3Pct;
   if(peakPoints>=ProfitLockTier4Mult*unit) best=ProfitLockTier4Pct;
   return best;
}

//===================== УПРАВЛЕНИЕ ОТКРЫТОЙ СДЕЛКОЙ ======
void ManageOpenPositions()
{
   CleanupPeakProfit();
   CleanupQuickStages(); // п.25: чистим память по ступеням быстрой фиксации
   double atr=GetATRValue(); // пересчитывается каждый тик -> трейлинг сам сужается при затухании ATR (п.4)
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=(long)MagicNumber) continue;

      long type=PositionGetInteger(POSITION_TYPE);
      double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL=PositionGetDouble(POSITION_SL);
      double currentTP=PositionGetDouble(POSITION_TP);
      double volume=PositionGetDouble(POSITION_VOLUME);
      double price=(type==POSITION_TYPE_BUY)?SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      double profitPoints=(type==POSITION_TYPE_BUY)?(price-openPrice)/_Point:(openPrice-price)/_Point;
      double peakPoints=UpdatePeakProfit(ticket,profitPoints); // максимум прибыли, который когда-либо был по этой сделке
      double riskPoints=UpdatePositionRisk(ticket,openPrice,currentSL); // "1R" сделки

      // п.4: минимальная дистанция брокера — без неё ModifyPosition вернёт ошибку
      long stopLevelPts=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
      double brokerMinDist=stopLevelPts*_Point;

      // Собираем лучший (самый защищающий прибыль) вариант SL из трёх источников
      // и применяем ОДНИМ вызовом — меньше нагрузки на сервер, нет гонки между модификациями.
      double bestSL=currentSL;

      // п.24: авто-масштабирование порогов под ATR текущего инструмента (см. Config.mqh/Indicators.mqh)
      double effBEOffsetPts    = EffPointsThreshold(BreakEvenOffsetPoints,0.05);
      double effTrailMinPts    = EffPointsThreshold(TrailingMinPoints,0.3);
      double effProfitLockPts  = EffPointsThreshold(ProfitLockStartPoints,0.15);
      double effTrailStepPts   = EffPointsThreshold(TrailingStepMinPoints,0.02);

      // Порог Profit Lock не может быть меньше доли ОТ РИСКА СДЕЛКИ (1R) —
      // см. диагноз у ProfitLockStartRFraction в Config.mqh.
      if(riskPoints>0)
         effProfitLockPts=MathMax(effProfitLockPts,riskPoints*ProfitLockStartRFraction);

      // 1) Break Even
      double beTriggerPts=(atr*BreakEvenATRMultiplier)/_Point;
      if(UseBreakEven && profitPoints>=beTriggerPts)
      {
         double beSL=(type==POSITION_TYPE_BUY)?openPrice+effBEOffsetPts*_Point:openPrice-effBEOffsetPts*_Point;
         bestSL=BetterSL(type,bestSL,beSL);
      }

      // 2) ATR-трейлинг — гонится за ТЕКУЩЕЙ ценой, дистанция сужается/расширяется вместе с ATR
      double trailPts=MathMax(effTrailMinPts,(atr*TrailingATRMultiplier)/_Point);
      if(UseTrailingStop && profitPoints>=trailPts)
      {
         double trailSL=(type==POSITION_TYPE_BUY)?price-trailPts*_Point:price+trailPts*_Point;
         bestSL=BetterSL(type,bestSL,trailSL);
      }

      // 3) Profit Lock (п.12) — гонится за ПИКОВОЙ прибылью, а не текущей ценой.
      // Гарантирует, что SL не отстанет от максимума больше чем на ProfitLockPercent,
      // даже если цена резко развернулась и ATR-трейлинг ещё не успел среагировать.
      if(UseProfitLockTrailing && peakPoints>=effProfitLockPts)
      {
         double lockPct=TieredLockPercent(peakPoints,effProfitLockPts);
         double lockPoints=peakPoints*lockPct/100.0;
         double lockSL=(type==POSITION_TYPE_BUY)?openPrice+lockPoints*_Point:openPrice-lockPoints*_Point;
         bestSL=BetterSL(type,bestSL,lockSL);
      }

      bool improved = (type==POSITION_TYPE_BUY) ? (bestSL>currentSL) : (currentSL==0 || bestSL<currentSL);
      bool distOk   = (brokerMinDist<=0) || (MathAbs(price-bestSL)>=brokerMinDist);
      bool stepOk   = (currentSL==0) || MathAbs(bestSL-currentSL)>=effTrailStepPts*_Point;
      if(bestSL!=currentSL && improved && distOk && stepOk)
         trade.PositionModify(ticket,bestSL,currentTP);

      // Частичное закрытие (старый механизм, по фикс. пунктам; по умолчанию выключен)
      // п.25: объём считается через FloorVolumeToStep — прежняя формула
      // MathFloor(v/step)*step из-за двоичной арифметики теряла шаг объёма.
      if(UsePartialClose && !IsPartialClosed(ticket) && profitPoints>=PartialCloseTriggerPoints)
      {
         if(CloseVolumePercent(ticket,volume,PartialClosePercent))
         {
            MarkPartialClosed(ticket);
            continue; // объём изменился — остальное досчитаем на следующем тике
         }
      }

      // п.25: БЫСТРАЯ ФИКСАЦИЯ ПРИБЫЛИ ступенями по ATR.
      // Снимаем часть прибыли рано, остаток продолжает идти под трейлингом.
      // Пороги в ATR, поэтому одинаково работают на EURUSD и на золоте.
      if(UseQuickProfit && atr>0)
      {
         int stage=GetQuickStage(ticket);
         double qp1Pts=QuickProfit1ATR*atr/_Point;
         double qp2Pts=QuickProfit2ATR*atr/_Point;

         if(stage<1 && QuickProfit1ATR>0 && profitPoints>=qp1Pts)
         {
            if(CloseVolumePercent(ticket,volume,QuickProfit1Percent))
            {
               SetQuickStage(ticket,1);
               PrintFormat("Быстрая фиксация 1: закрыто %.0f%% при +%.0f пт (порог %.0f)",
                           QuickProfit1Percent, profitPoints, qp1Pts);
               continue;
            }
         }
         else if(stage<2 && QuickProfit2ATR>0 && profitPoints>=qp2Pts)
         {
            if(CloseVolumePercent(ticket,volume,QuickProfit2Percent))
            {
               SetQuickStage(ticket,2);
               PrintFormat("Быстрая фиксация 2: закрыто %.0f%% при +%.0f пт (порог %.0f)",
                           QuickProfit2Percent, profitPoints, qp2Pts);
               continue;
            }
         }
      }

      // п.25: ВЫХОД ПО ВРЕМЕНИ — "долго не быть в сделке".
      // Мягкий: время вышло и сделка в плюсе -> фиксируем прибыль.
      //         Если сделка прямо сейчас на пике (ещё растёт) — не трогаем.
      // Жёсткий: время вышло совсем -> закрываем в любом случае.
      if(UseTimeExit)
      {
         datetime openTime=(datetime)PositionGetInteger(POSITION_TIME);
         double heldMinutes=(double)(TimeCurrent()-openTime)/60.0;

         if(HardExitMinutes>0 && heldMinutes>=HardExitMinutes)
         {
            if(trade.PositionClose(ticket))
               PrintFormat("Жёсткий выход по времени: %.0f мин в сделке, результат %.0f пт",
                           heldMinutes, profitPoints);
            continue;
         }

         if(SoftExitMinutes>0 && heldMinutes>=SoftExitMinutes &&
            profitPoints>0 && profitPoints>=SoftExitMinProfitPoints)
         {
            // "Сделка ещё растёт" = текущая прибыль равна пиковой
            bool stillRunning = SoftExitKeepRunning && (profitPoints>=peakPoints-0.5);
            if(!stillRunning)
            {
               if(trade.PositionClose(ticket))
                  PrintFormat("Мягкий выход по времени: %.0f мин, зафиксировано +%.0f пт (пик был %.0f)",
                              heldMinutes, profitPoints, peakPoints);
               continue;
            }
         }
      }
   }
}
