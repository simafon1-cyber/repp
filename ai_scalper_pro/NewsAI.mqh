//+------------------------------------------------------------------+
//| NewsAI.mqh                                                       |
//| Новости и внешний AI-сигнал (п.11):                              |
//|  1) Жёсткий блок по HIGH-новости — встроенный календарь MT5,     |
//|     бесплатно, без API-ключей.                                   |
//|  2) Мягкий штраф score за MODERATE-новость рядом.                |
//|  3) Плагин под сторонний источник (бридж/AI) через WebRequest —  |
//|     сейчас можно подключить бесплатный локальный источник,       |
//|     позже просто сменить URL на платный/качественный.            |
//+------------------------------------------------------------------+

//===================== ЖЁСТКИЙ БЛОК ПО НОВОСТЯМ =========
bool NewsFilterOk()
{
   if(g_effIgnoreSoftFilters) return true; // "Истеричка" (п.17) — торгует и на новостях тоже
   if(!UseNewsFilter) return true;
   string baseCur=SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE);
   string quoteCur=SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   datetime from=TimeCurrent()-NewsWindowMinutes*60;
   datetime to  =TimeCurrent()+NewsWindowMinutes*60;
   string currencies[2]; currencies[0]=baseCur; currencies[1]=quoteCur;
   for(int c=0;c<2;c++)
   {
      MqlCalendarValue values[];
      if(CalendarValueHistory(values, from, to, NULL, currencies[c]))
      {
         for(int i=0;i<ArraySize(values);i++)
         {
            MqlCalendarEvent event;
            if(CalendarEventById(values[i].event_id, event))
               if(event.importance==CALENDAR_IMPORTANCE_HIGH) return false;
         }
      }
   }
   return true;
}

//===================== МЯГКИЙ НОВОСТНОЙ ШТРАФ ============
// Бесплатно и без API-ключей: используем встроенный календарь MT5.
// В отличие от NewsFilterOk (жёсткий блок на HIGH), здесь MODERATE-новость
// рядом не запрещает сделку, а просто немного снижает score.
double NewsSoftPenalty()
{
   if(!UseNewsScoreSoft) return 0;
   string baseCur =SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE);
   string quoteCur=SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   datetime from=TimeCurrent()-NewsWindowMinutes*60;
   datetime to  =TimeCurrent()+NewsWindowMinutes*60;
   string currencies[2]; currencies[0]=baseCur; currencies[1]=quoteCur;
   for(int c=0;c<2;c++)
   {
      MqlCalendarValue values[];
      if(CalendarValueHistory(values, from, to, NULL, currencies[c]))
      {
         for(int i=0;i<ArraySize(values);i++)
         {
            MqlCalendarEvent event;
            if(CalendarEventById(values[i].event_id, event))
               if(event.importance==CALENDAR_IMPORTANCE_MODERATE) return NewsScorePenalty;
         }
      }
   }
   return 0;
}

//===================== ВНЕШНИЙ AI/НОВОСТНОЙ СИГНАЛ (плагин) ========
// Задача этого блока — дать простую точку подключения стороннего источника:
// сейчас можно поднять локальный bridge-скрипт (Python и т.п.), который сам
// бесплатно тянет новости/сентимент (RSS, календарь, free news API) и отдаёт
// по HTTP простой JSON {"direction":"buy|sell|neutral","confidence":0.0..1.0}.
// Позже — просто меняешь ExternalSignalURL на нормальный платный AI-источник,
// код EA трогать не нужно. Если источник недоступен — сигнал ИГНОРИРУЕТСЯ
// (fail-open), торговля по обычному score продолжается как обычно.

string JsonGetString(string json,string key)
{
   string pattern="\""+key+"\"";
   int p=StringFind(json,pattern);
   if(p<0) return "";
   int colon=StringFind(json,":",p);
   if(colon<0) return "";
   int q1=StringFind(json,"\"",colon+1);
   if(q1<0) return "";
   int q2=StringFind(json,"\"",q1+1);
   if(q2<0) return "";
   return StringSubstr(json,q1+1,q2-q1-1);
}
double JsonGetDouble(string json,string key)
{
   string pattern="\""+key+"\"";
   int p=StringFind(json,pattern);
   if(p<0) return 0;
   int colon=StringFind(json,":",p);
   if(colon<0) return 0;
   int len=StringLen(json);
   int start=colon+1;
   while(start<len && StringGetCharacter(json,start)==' ') start++;
   int end=start;
   while(end<len)
   {
      ushort ch=StringGetCharacter(json,end);
      if((ch>='0'&&ch<='9') || ch=='.' || ch=='-') end++;
      else break;
   }
   if(end<=start) return 0;
   return StringToDouble(StringSubstr(json,start,end-start));
}

// Дёргаем источник не чаще раза в ExternalSignalRefreshSec секунд — новости
// не меняются каждый тик, а WebRequest — блокирующий вызов, злоупотреблять им
// нельзя. Интервал также определяет расход лимита запросов у поставщика данных.
bool FetchExternalSignal()
{
   if(!UseExternalSignal) return false;
   int refreshSec=MathMax(10,ExternalSignalRefreshSec); // ниже 10 с смысла нет
   if(g_extLastFetch>0 && TimeCurrent()-g_extLastFetch<refreshSec) return g_extLastOk;

   string headers="Content-Type: application/json\r\n";
   char post[]; char result[]; string resultHeaders;
   string url=ExternalSignalURL+"?symbol="+_Symbol;

   ResetLastError();
   int res=WebRequest("GET", url, headers, ExternalSignalTimeoutMs, post, result, resultHeaders);
   g_extLastFetch=TimeCurrent();

   if(res==-1)
   {
      int err=GetLastError();
      if(err==4060)
         Print("Внешний сигнал: URL не разрешён. Добавь ", ExternalSignalURL,
               " в Tools -> Options -> Expert Advisors -> Allow WebRequest for listed URL");
      else
         PrintFormat("Внешний сигнал: ошибка WebRequest %d", err);
      g_extLastOk=false;
      return false;
   }

   string json=CharArrayToString(result);
   string dir=JsonGetString(json,"direction");
   if(dir=="")
   {
      g_extLastOk=false;
      return false;
   }

   g_extLastDirection  = dir;
   g_extLastConfidence = MathMax(0,MathMin(1,JsonGetDouble(json,"confidence")));
   g_extLastOk = true;
   return true;
}

// п.24: вес внешнего AI-сигнала не фиксирован — авто-адаптируется под текущий
// подтверждённый режим рынка. Собственный паттерн EA (откат+пробой) менее
// надёжен во ФЛЭТЕ (см. RegimeScoreAdjustment в MarketRegime.mqh) — значит
// мнение AI там логично весить больше. В подтверждённом ТРЕНДЕ структура входа
// и так надёжна — вес AI немного снижается. Это не меняет НАПРАВЛЕНИЕ решения
// AI, только то, сколько баллов оно может добавить/вычесть.
double EffectiveExternalSignalWeight()
{
   double w=ExternalSignalWeight;
   if(UseMarketRegimeFilter)
   {
      if(g_currentRegime==REGIME_RANGE)      w*=1.3;
      else if(g_currentRegime==REGIME_TREND) w*=0.85;
   }
   return w;
}

// Применяет кэшированный внешний сигнал к уже посчитанному score конкретного направления.
double ApplyExternalSignal(int direction,double score)
{
   if(!UseExternalSignal || !g_extLastOk) return score; // источник выключен/недоступен — ничего не портим

   string want=(direction==1)?"buy":"sell";

   if(ExternalSignalRequireDirection && g_extLastDirection!=want)
      return 0; // жёсткий режим: без совпадения направления сделки по этому direction не будет

   double weight=EffectiveExternalSignalWeight();
   double delta=0;
   if(g_extLastDirection==want)            delta= weight*g_extLastConfidence;
   else if(g_extLastDirection=="neutral")  delta=0;
   else                                    delta=-weight*g_extLastConfidence; // сигнал против

   return MathMax(0,MathMin(100, score+delta));
}

//===================== НОВОСТНОЙ ПРОБОЙ — режим MODE_NEWS_TRADING/MODE_BOTH (п.24) ===
// В отличие от NewsFilterOk (избегает новостей), здесь мы АКТИВНО ищем HIGH-
// импакт событие за последние NewsBreakoutWindowMinutes минут и проверяем,
// не является ли текущая ЗАКРЫТАЯ свеча сильной направленной реакцией на него
// (большое тело, пробой экстремума предыдущей свечи — типичная реакция рынка
// на NFP/ставки/CPI и т.п.). Уверенность растёт вместе с силой тела свечи и
// сверяется с порогом активного риск-профиля (g_effMinScoreToTrade) — так
// более осторожные профили требуют более чёткую реакцию рынка, а не входят
// на любое шевеление цены сразу после новости.
bool DetectNewsBreakout(int &direction,double &confidence)
{
   direction=0; confidence=0;

   string baseCur =SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE);
   string quoteCur=SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   datetime now=TimeCurrent();
   datetime from=now-NewsBreakoutWindowMinutes*60;
   string currencies[2]; currencies[0]=baseCur; currencies[1]=quoteCur;

   bool freshHighImpact=false;
   for(int c=0;c<2 && !freshHighImpact;c++)
   {
      if(currencies[c]=="") continue;
      MqlCalendarValue values[];
      if(CalendarValueHistory(values, from, now, NULL, currencies[c]))
      {
         for(int i=0;i<ArraySize(values);i++)
         {
            MqlCalendarEvent event;
            if(CalendarEventById(values[i].event_id, event) && event.importance==CALENDAR_IMPORTANCE_HIGH)
            { freshHighImpact=true; break; }
         }
      }
   }
   if(!freshHighImpact) return false; // нет свежей важной новости — нечего ловить

   double o=iOpen(_Symbol,_Period,SIGNAL_SHIFT), c=iClose(_Symbol,_Period,SIGNAL_SHIFT);
   double h=iHigh(_Symbol,_Period,SIGNAL_SHIFT), l=iLow(_Symbol,_Period,SIGNAL_SHIFT);
   double range=h-l; if(range<=0) return false;
   double bodyPct=MathAbs(c-o)/range*100.0;
   if(bodyPct<NewsBreakoutMinBodyPercent) return false; // свеча недостаточно направленная

   double prevHigh=iHigh(_Symbol,_Period,SIGNAL_SHIFT+1);
   double prevLow =iLow(_Symbol,_Period,SIGNAL_SHIFT+1);

   int dir=0;
   if(c>o && h>prevHigh)      dir=1;
   else if(c<o && l<prevLow)  dir=-1;
   if(dir==0) return false; // тело сильное, но экстремум предыдущей свечи не пробит

   double conf=MathMin(100.0, 40.0+bodyPct*0.6); // сильнее тело -> выше уверенность
   if(conf<g_effMinScoreToTrade) return false; // не дотягивает даже до порога активного риск-профиля

   direction=dir;
   confidence=conf;
   return true;
}
