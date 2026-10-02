'use strict';
(function(global) {
    const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const activeStates = new Set(['queued','running','cancelling']);
    class ResearchWorkspace {
        constructor() { this.chartCache=new Map(); this.active=false; this.epoch=0; this.view='formulas'; this.data={}; this.jobId=null; this.timer=null; this.chartEpoch=0; this.inflight=new Map(); }
        node(id) { return document.getElementById(`research-${id}`); }
        value(id) { return this.node(id)?.value || ''; }
        status(message, error=false) { this.node('status').textContent=message; this.node('status').classList.toggle('error',error); }
        async api(path, payload, signal=this.controller?.signal) {
            const options={signal,headers:{'X-Quant-Session':document.querySelector('meta[name="quant-session-token"]')?.content||''}};
            if (payload!==undefined) { options.method='POST'; options.headers['Content-Type']='application/json'; options.body=JSON.stringify(payload); }
            const bounded=new AbortController(); let timedOut=false;
            const abort=()=>bounded.abort();
            if(signal?.aborted) abort(); else signal?.addEventListener('abort',abort,{once:true});
            const timeout=global.setTimeout(()=>{ timedOut=true; bounded.abort(); },30000);
            options.signal=bounded.signal;
            try {
                const res=await fetch(path.startsWith('/api/')?path:`/api/research/${path}`,options);
                const body=await res.json();
                if (!res.ok||!body.success) throw new Error(body.error||`HTTP ${res.status}`);
                return body.job_id ? body : body.data;
            } catch(error) { if(timedOut) throw new Error('研究请求超时，请重试'); throw error; }
            finally { global.clearTimeout(timeout); signal?.removeEventListener('abort',abort); }
        }
        async activate() {
            const root=document.getElementById('research-page');
            const supported=(global.quantMarketContext?.currentMarket?.()||'a_share')==='a_share';
            root.querySelector('.research-content').hidden=!supported;
            this.node('unavailable').hidden=supported;
            if(!supported) { if(this.active) this.deactivate(); return; }
            if (this.active) return;
            this.active=true; this.controller=new AbortController(); this.epoch++;
            if (!this.mounted) {
                this.root=document.getElementById('research-page');
                this.click=event=> { const tab=event.target.closest('[data-research-view]'); if(tab) this.setView(tab.dataset.researchView);
                    const action=event.target.closest('[data-research-action]'); if(action) this.act(action.dataset.researchAction,action).catch(error=>this.handle(error)); };
                this.change=event=>this.changed(event.target).catch(error=>this.handle(error));
                this.resize=()=> { this.priceChart?.resize(); this.navChart?.resize(); };
                this.root.addEventListener('click',this.click); this.root.addEventListener('change',this.change); global.addEventListener('resize',this.resize); this.mounted=true;
            }
            await this.refresh(); if(this.jobId) this.poll();
        }
        deactivate() {
            this.active=false; this.epoch++; this.chartEpoch++; this.controller?.abort(); this.chartController?.abort(); this.inflight.clear();
            global.clearTimeout(this.timer); this.timer=null; this.priceChart?.dispose(); this.navChart?.dispose(); this.priceChart=null; this.navChart=null;
            if(this.mounted) { this.root.removeEventListener('click',this.click); this.root.removeEventListener('change',this.change); global.removeEventListener('resize',this.resize); this.mounted=false; }
        }
        handle(error) { if(error.name!=='AbortError'&&this.active) this.status(error.message,true); }
        setView(view) {
            this.view=view; this.root.querySelectorAll('[data-research-panel]').forEach(panel=>panel.hidden=panel.dataset.researchPanel!==view);
            this.root.querySelectorAll('[data-research-view]').forEach(button=>button.classList.toggle('active',button.dataset.researchView===view));
            if(view==='chart') this.loadChart().catch(error=>this.handle(error));
            if(view==='account') this.loadAccount().catch(error=>this.handle(error));
        }
        fill(id, items, label, blank='') {
            const select=this.node(id),old=select.value;
            select.innerHTML=(blank?`<option value="">${esc(blank)}</option>`:'')+items.map(item=>`<option value="${esc(item.id)}">${esc(label(item))}</option>`).join('');
            if(items.some(item=>item.id===old)) select.value=old;
        }
        async refresh() {
            const epoch=this.epoch;
            const kinds=['formulas','snapshots','runs','pools','markers','sessions','checkpoints','accounts'];
            const pages=await Promise.all(kinds.map(kind=>this.api(kind)));
            if(!this.active||epoch!==this.epoch) return;
            kinds.forEach((kind,index)=>this.data[kind]=pages[index].items);
            this.fill('formula',this.data.formulas.filter(item=>!item.archived),item=>`${item.name} / v${item.revision}`,'新建公式');
            this.fill('snapshot',this.data.snapshots,item=>`${item.id.slice(0,8)} · ${item.provider} · ${item.instrument_count}只 · ${item.price_view}`,'选择冻结数据');
            this.fill('run',this.data.runs,item=>`${item.id.slice(0,8)} · ${item.status} · ${item.selected_count}条`,'选择历史运行');
            this.fill('pool',this.data.pools.filter(item=>!item.archived&&(!item.session_id||item.session_id===this.value('session'))),item=>`${item.name} (${item.member_count})`,'选择研究池');
            this.fill('session',this.data.sessions,item=>`${item.id.slice(0,8)} · ${item.as_of} ${item.phase}`,'实时研究');
            this.fill('checkpoint',this.data.checkpoints,item=>`${item.id.slice(0,8)} · ${item.session_id.slice(0,8)}`,'选择检查点');
            this.fill('account',this.data.accounts.filter(item=>item.session_id===this.value('session')),item=>`${item.id.slice(0,8)} · ¥${item.cash}`,'选择模拟账户');
            this.renderClock(); await Promise.all([this.loadRun(),this.loadPool()]);
        }
        parameters(id) { const value=JSON.parse(this.value(id)||'{}'); if(!value||Array.isArray(value)||typeof value!=='object') throw new Error('参数必须是 JSON 对象'); return value; }
        session() { return (this.data.sessions||[]).find(item=>item.id===this.value('session')); }
        renderClock() {
            const session=this.session(); this.node('clock').textContent=session?`${session.as_of} / ${session.phase==='open'?'开盘前':'收盘后'} / ${session.mode}`:'实时研究 / 冻结版本';
            this.node('session-detail').textContent=session?JSON.stringify(session,null,2):'';
        }
        async changed(target) {
            if(target.id==='research-formula') {
                const formula=(this.data.formulas||[]).find(item=>item.id===target.value);
                if(formula) { for(const field of ['name','description','source']) this.node(`formula-${field}`).value=formula[field]||''; this.node('formula-parameters').value=JSON.stringify(formula.parameters); this.node('formula-revision').value=formula.revision; }
            }
            if(target.id==='research-run') { await this.loadRun(); if(this.selectedRun) this.node('snapshot').value=this.selectedRun.snapshot_id; }
            if(target.id==='research-pool'||target.id==='research-hide-cooling') { await this.loadPool(); await this.loadRun(); }
            if(target.id==='research-account') await this.loadAccount();
            if(target.id==='research-session') {
                if(this.jobId) await this.api(`/api/select/cancel/${this.jobId}`,{});
                const session=this.session(); if(session) this.node('snapshot').value=session.snapshot_id;
                this.renderClock(); await this.refresh();
                if(session?.execution?.formula_id) {
                    const settings=session.execution;
                    if(!Array.from(this.node('formula').options).some(option=>option.value===settings.formula_id)) {
                        this.node('formula').add(new Option(`${settings.formula_name} / 会话冻结版本`,settings.formula_id));
                    }
                    this.node('formula').value=settings.formula_id;
                    const formula=await this.api(`formulas/${settings.formula_id}?revision=${settings.formula_revision}`);
                    for(const field of ['name','description','source']) this.node(`formula-${field}`).value=formula[field]||'';
                    this.node('formula-parameters').value=JSON.stringify(formula.parameters);
                    this.node('formula-revision').value=settings.formula_revision;
                    this.node('run-parameters').value=JSON.stringify(settings.parameters);
                    this.node('backend').value=settings.backend;
                    this.node('score').value=settings.score||'';
                }
                if(this.view==='chart') await this.loadChart(); if(this.view==='account') await this.loadAccount();
            }
            if(['research-symbol','research-snapshot'].includes(target.id)&&this.view==='chart') await this.loadChart();
        }
        async launch(path,payload) {
            const result=await this.api(path,payload); this.jobContext=[this.value('snapshot'),this.value('session'),this.session()?.as_of,this.session()?.phase]; this.jobId=result.job_id; this.node('cancel').hidden=false; this.status(`任务 ${this.jobId} 已提交`); this.poll();
        }
        async poll() {
            if(!this.active||!this.jobId) return;
            const epoch=this.epoch;
            try {
                const job=await this.api(`/api/select/status/${this.jobId}`);
                if(epoch!==this.epoch||!this.active) return;
                this.status(`${job.current_step||'研究任务'} · ${job.status} · ${job.progress_pct||0}%`,job.status==='error');
                if(activeStates.has(job.status)) this.timer=global.setTimeout(()=>this.poll(),600);
                else {
                    this.jobId=null; this.node('cancel').hidden=true;
                    await this.refresh(); const result=job.research_result;
                    const context=[this.value('snapshot'),this.value('session'),this.session()?.as_of,this.session()?.phase];
                    if(result?.id&&JSON.stringify(context)===JSON.stringify(this.jobContext)) {
                        if(result.snapshot_id) { this.node('run').value=result.id; await this.loadRun(); }
                        else this.node('snapshot').value=result.id;
                    }
                    if(job.error) this.status(job.error,true);
                }
            } catch(error) { this.handle(error); if(this.active&&this.jobId) this.timer=global.setTimeout(()=>this.poll(),2000); }
        }
        async act(action,button) {
            if(button) button.disabled=true;
            try {
                let result;
                const session=this.session(), formula=this.value('formula');
                switch(action) {
                    case 'refresh': await this.refresh(); this.status('研究记录已刷新'); break;
                    case 'new-formula': this.node('formula-revision').value=''; this.node('formula').value=''; this.node('formula-name').value=''; this.node('formula-description').value=''; this.node('formula-source').value=''; break;
                    case 'save-formula': result=await this.api(`formulas${formula?`/${formula}`:''}`,{name:this.value('formula-name'),description:this.value('formula-description'),source:this.value('formula-source'),parameters:this.parameters('formula-parameters'),markets:['a_share']}); await this.refresh(); this.node('formula').value=result.id; this.node('formula-revision').value=result.revision; this.status(`已保存公式 v${result.revision}`); break;
                    case 'archive-formula': if(!formula) throw new Error('先选择公式'); await this.api(`formulas/${formula}/archive`,{}); await this.refresh(); break;
                    case 'capture': { const values=this.value('capture-symbols').split(',').map(item=>item.trim()).filter(Boolean); await this.launch('capture',values.length?{symbols:values}:{}); break; }
                    case 'import-archive': { const file=this.node('archive-file').files[0]; if(!file) throw new Error('请选择档案'); if(file.size>10*1024*1024) throw new Error('档案最大10 MiB'); await this.launch('archives',JSON.parse(await file.text())); break; }
                    case 'test-formula': if(!formula||!this.value('snapshot')) throw new Error('先选择公式和快照'); await this.launch(`formulas/${formula}/test`,{snapshot_id:this.value('snapshot'),symbol:this.value('symbol'),revision:Number(this.value('formula-revision'))||undefined,parameters:this.parameters('run-parameters')}); break;
                    case 'run': if(!formula||!this.value('snapshot')) throw new Error('先保存公式并选择冻结输入'); await this.launch('runs',{formula_id:formula,formula_revision:Number(this.value('formula-revision'))||undefined,snapshot_id:this.value('snapshot'),session_id:session?.id,parameters:this.parameters('run-parameters'),backend:this.value('backend'),score:this.value('score')||null}); break;
                    case 'cancel': if(this.jobId) await this.api(`/api/select/cancel/${this.jobId}`,{}); break;
                    case 'initialize-pools': await this.api('pools/initialize',{}); await this.refresh(); this.status('研究池已就绪，原自选文件保留'); break;
                    case 'save-pool': result=await this.api('pools',{name:this.value('pool-name'),session_id:session?.id}); await this.refresh(); this.node('pool').value=result.id; break;
                    case 'rename-pool': if(!this.value('pool')) throw new Error('先选择研究池'); await this.api(`pools/${this.value('pool')}`,{name:this.value('pool-name')}); await this.refresh(); break;
                    case 'add-member': await this.addMember(button?.dataset.symbol||this.value('symbol')); break;
                    case 'remove-member': await this.api(`pools/${this.value('pool')}/members`,{market:button.dataset.market,symbol:button.dataset.symbol,remove:true}); await this.refresh(); break;
                    case 'cooldown': await this.api('markers',{symbol:this.value('symbol'),cooldown_until:this.value('cooldown')||null,reason:this.value('note')}); await this.refresh(); this.status('冷却标记已保存，原始结果保持可查'); break;
                    case 'chart': await this.loadChart(); break;
                    case 'create-session': result=await this.api('sessions',{snapshot_id:this.value('snapshot'),as_of:this.value('asof'),phase:this.value('phase'),mode:this.value('replay-mode')}); await this.refresh(); this.node('session').value=result.id; await this.changed(this.node('session')); break;
                    case 'advance': if(!session) throw new Error('先选择历史会话'); if(this.jobId) await this.api(`/api/select/cancel/${this.jobId}`,{}); await this.api(`sessions/${session.id}/advance`,{}); await this.refresh(); if(this.view==='account') await this.loadAccount(); break;
                    case 'checkpoint': if(!session) throw new Error('先选择历史会话'); result=await this.api(`sessions/${session.id}/checkpoint`,{}); await this.refresh(); this.node('checkpoint').value=result.id; break;
                    case 'restore': {
                        if(!this.value('checkpoint')) throw new Error('先选择检查点');
                        result=await this.api(`checkpoints/${this.value('checkpoint')}/restore`,{});
                        await this.refresh(); this.node('session').value=result.id; await this.changed(this.node('session'));
                        const accounts=this.data.accounts.filter(item=>item.session_id===result.id);
                        if(accounts.length===1) { this.node('account').value=accounts[0].id; await this.loadAccount(); }
                        this.status(`已从检查点创建分支 ${result.id.slice(0,8)}`); break;
                    }
                    case 'create-account': if(!session) throw new Error('先选择历史会话'); result=await this.api('accounts',{session_id:session.id,cash:Number(this.value('cash')),assumptions:this.parameters('assumptions')}); await this.refresh(); this.node('account').value=result.id; await this.loadAccount(); break;
                    case 'order': if(!this.value('account')) throw new Error('先选择模拟账户'); await this.api(`accounts/${this.value('account')}/orders`,{symbol:this.value('symbol'),side:this.value('order-side'),quantity:Number(this.value('order-quantity')),request_id:crypto.randomUUID()}); await this.loadAccount(); break;
                    case 'settle': case 'mark': if(!this.value('account')) throw new Error('先选择模拟账户'); await this.api(`accounts/${this.value('account')}/${action}`,{}); await this.loadAccount(); break;
                    case 'cancel-order': await this.api(`accounts/${this.value('account')}/orders/${button.dataset.order}/cancel`,{}); await this.loadAccount(); break;
                }
            } finally { if(button) button.disabled=false; }
        }
        async addMember(symbol) {
            if(!this.value('pool')) throw new Error('先选择研究池');
            await this.api(`pools/${this.value('pool')}/members`,{symbol,market:'a_share',note:this.value('note'),run_id:this.value('run')||null}); await this.refresh(); this.status(`${symbol} 已加入当前池`);
        }
        cooling(symbol,market='a_share') { const today=new Date().toLocaleDateString('en-CA'); return (this.data.markers||[]).find(item=>item.market===market&&item.symbol===symbol&&item.cooldown_until>=today); }
        async loadRun() {
            const id=this.value('run'),epoch=this.epoch; if(!id) { this.selectedRun=null; this.node('results').textContent='尚未选择运行。'; this.node('run-detail').textContent=''; return; }
            const run=await this.api(`runs/${id}`); if(!this.active||epoch!==this.epoch||id!==this.value('run')) return;
            this.node('run-detail').textContent=JSON.stringify(run,null,2); this.selectedRun=run;
            let rows=[]; for(const [strategy,items] of Object.entries(run.results||{})) for(const item of items) {
                const cooling=this.cooling(item.symbol,item.market); if(this.node('hide-cooling').checked&&cooling) continue;
                rows.push(`<tr><td>${esc(strategy)}</td><td>${esc(item.symbol)}</td><td>${esc(item.name)}</td><td>${esc(item.score??'—')}</td><td>${esc(cooling?`冷却至${cooling.cooldown_until}`:item.signals.map(signal=>(signal.reasons||[]).join(' · ')).join('; '))}</td><td><button class="btn btn-ghost" data-research-action="add-member" data-symbol="${esc(item.symbol)}">加入当前池</button></td></tr>`);
            }
            this.node('results').innerHTML=rows.length?`<div class="research-table-scroll"><table><thead><tr><th>策略</th><th>证券</th><th>名称</th><th>分数</th><th>原因/冷却</th><th>研究池</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`:'该运行未命中证券，或被当前研究视图过滤；原始结果见下方记录。';
        }
        async loadPool() {
            const id=this.value('pool'),epoch=this.epoch; if(!id) { this.node('members').textContent='选择研究池以查看成员。'; return; }
            const pool=await this.api(`pools/${id}`); if(!this.active||epoch!==this.epoch||id!==this.value('pool')) return;
            let rows=Object.values(pool.members).filter(item=>!this.node('hide-cooling').checked||!this.cooling(item.symbol,item.market));
            this.node('members').innerHTML=`<div class="research-table-scroll"><table><thead><tr><th>市场</th><th>证券</th><th>备注</th><th>加入时间</th><th>操作</th></tr></thead><tbody>${rows.map(item=>`<tr><td>${esc(item.market)}</td><td>${esc(item.symbol)}</td><td>${esc(item.note)}</td><td>${esc(item.added_at)}</td><td><button class="btn btn-ghost" data-research-action="remove-member" data-market="${esc(item.market)}" data-symbol="${esc(item.symbol)}">从当前池移除</button></td></tr>`).join('')}</tbody></table></div>`;
        }
        async loadChart() {
            const key=JSON.stringify([this.value('snapshot'),this.value('symbol'),this.value('range-start'),this.value('range-end'),this.session()?.revision,this.value('session'),this.selectedRun?.id,this.value('account'),this.selectedAccount?.account.revision]);
            if(this.inflight.has(key)) return this.inflight.get(key);
            const pending=this.renderChart().finally(()=>this.inflight.delete(key));
            this.inflight.set(key,pending); return pending;
        }
        async renderChart() {
            if(!this.value('snapshot')||!this.value('symbol')||!this.active) return;
            const chartEpoch=++this.chartEpoch; this.chartController?.abort(); this.chartController=new AbortController();
            this.priceChart?.clear(); this.node('signal-detail').textContent='';
            this.node('range').textContent=`正在读取 ${this.value('symbol')} 的冻结行情…`;
            const session=this.session(); const query=new URLSearchParams({snapshot_id:this.value('snapshot'),symbol:this.value('symbol')});
            for(const [key,id] of [['start','range-start'],['end','range-end']]) if(this.value(id)) query.set(key,this.value(id));
            if(session) { if(session.snapshot_id!==this.value('snapshot')) throw new Error('会话与图表数据版本不同'); query.set('as_of',session.as_of); query.set('phase',session.phase); }
            if(this.selectedRun?.snapshot_id===this.value('snapshot')) query.set('run_id',this.selectedRun.id);
            if(this.value('account')&&session?.snapshot_id===this.value('snapshot')) query.set('account_id',this.value('account'));
            query.set('account_revision',String(this.selectedAccount?.account.revision||0));
            for(const flag of ['0','1']) {
            query.set('indicators',flag);
            const cacheKey=query.toString();
            let data=this.chartCache.get(cacheKey);
            if(!data) {
                data=await this.api(`chart?${query}`,undefined,this.chartController.signal);
                this.chartCache.set(cacheKey,data);
                if(this.chartCache.size>20) this.chartCache.delete(this.chartCache.keys().next().value);
            }
            if(!this.active||chartEpoch!==this.chartEpoch) return;
            const volumeUnit={shares:'股',hands:'手',provider_native:'供应商原单位'}[data.volume_unit]||data.volume_unit;
            const priceView=data.display_price_view==='qfq'?`前复权（锚点 ${data.adjustment_anchor}）`:data.display_price_view;
            this.node('range').textContent=data.range?`${data.range.first_date} 至 ${data.range.last_date} · ${data.range.bars}根 · 收益 ${data.range.return_pct?.toFixed(2)??'—'}% · 振幅 ${data.range.amplitude_pct?.toFixed(2)??'—'}% · 成交量 ${data.range.volume} ${volumeUnit} · ${priceView} · ${data.source}`:'此时点没有可见行情';
            if(!global.echarts) throw new Error('图表库尚未就绪');
            this.priceChart ||= global.echarts.init(this.node('chart'),'dark');
            const dates=data.candles.map(item=>item.date);
            const hasVolume=data.indicators.some(item=>item.pane==='volume');
            const marks=[...data.signals,...data.trades.map(fill=>({date:fill.date,strategy:`${fill.side} ${fill.quantity}`,order_id:fill.order_id,rule_version:fill.rule_version,price:fill.price}))];
            this.priceChart.setOption({
                backgroundColor:'transparent',animation:false,tooltip:{trigger:'axis'},
                legend:{data:['价格',...data.indicators.map(item=>item.name)]},
                grid:hasVolume?[{left:65,right:25,top:40,height:'48%'},{left:65,right:25,top:'65%',height:'15%'}]:[{left:65,right:25,bottom:65,top:40}],
                xAxis:(hasVolume?[0,1]:[0]).map(index=>({type:'category',gridIndex:index,data:dates,axisLabel:{show:!hasVolume||index===1}})),
                yAxis:hasVolume?[{scale:true,gridIndex:0},{scale:true,gridIndex:1,name:volumeUnit}]:[{scale:true}],
                dataZoom:[{type:'inside',xAxisIndex:hasVolume?[0,1]:[0]},{type:'slider',xAxisIndex:hasVolume?[0,1]:[0],bottom:8}],
                series:[{
                    name:'价格',type:'candlestick',data:data.candles.map(item=>[item.open,item.close,item.low,item.high]),
                    markPoint:{data:marks.filter(mark=>dates.includes(mark.date)).map(mark=>({coord:[mark.date,data.candles.find(item=>item.date===mark.date)?.high],value:mark.strategy,context:mark}))}
                },...data.indicators.map(item=>({
                    name:item.name,type:item.type==='bar'?'bar':'line',data:item.values,showSymbol:false,
                    xAxisIndex:item.pane==='volume'?1:0,yAxisIndex:item.pane==='volume'?1:0
                }))]
            },true);
            this.priceChart.off('click'); this.priceChart.on('click',event=>{ if(event.data?.context) this.node('signal-detail').textContent=JSON.stringify(event.data.context,null,2); }); this.priceChart.resize();
            if(flag==='1'&&this.view==='chart') this.status(`已加载 ${this.value('symbol')} 图表与区间统计`);
            }
        }
        async loadAccount() {
            const id=this.value('account'),epoch=this.epoch; if(!id) { this.selectedAccount=null; this.node('account-summary').textContent='在有原始价格的历史会话中创建模拟账户。'; this.node('orders').textContent=''; this.node('ledger').textContent=''; this.navChart?.clear(); return; }
            const report=await this.api(`accounts/${id}`); if(!this.active||epoch!==this.epoch||id!==this.value('account')) return;
            this.selectedAccount=report;
            this.node('account-summary').textContent=`现金 ¥${report.account.cash.toFixed(2)} · 可用 ¥${report.available_cash.toFixed(2)} · 净值 ${report.equity===null?'暂停发布':`¥${report.equity.toFixed(2)}`} · 回撤 ${report.max_drawdown_pct?.toFixed(2)??'—'}% · ${report.issues.join('; ')}`;
            this.node('ledger').textContent=JSON.stringify({positions:report.positions,fees:report.fees,receivables:report.account.receivables,assumptions:report.account.assumptions,events:report.account.events},null,2);
            this.node('orders').innerHTML=`<div class="research-table-scroll"><table><thead><tr><th>证券/方向</th><th>股数/剩余</th><th>状态</th><th>提交时点</th><th>原因/成交</th><th>操作</th></tr></thead><tbody>${report.account.orders.map(order=>`<tr><td>${esc(order.symbol)} ${esc(order.side)}</td><td>${order.quantity} / ${order.remaining}</td><td>${esc(order.status)}</td><td>${esc(order.submitted_as_of)} ${esc(order.submitted_phase)}</td><td>${esc(order.waiting_reason||order.fills.map(fill=>`${fill.date} ${fill.quantity}股 @${fill.price}`).join('; '))}</td><td>${['pending','partial'].includes(order.status)?`<button class="btn btn-ghost" data-research-action="cancel-order" data-order="${esc(order.id)}">撤单</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;
            if(this.view==='account'&&global.echarts) { this.navChart ||= global.echarts.init(this.node('nav'),'dark'); this.navChart.setOption({backgroundColor:'transparent',tooltip:{trigger:'axis'},xAxis:{type:'category',data:report.account.nav.map(point=>point.date)},yAxis:{scale:true},series:[{name:'净值',type:'line',data:report.account.nav.map(point=>point.equity)}]},true); this.navChart.resize(); }
        }
    }
    global.quantResearchWorkspace=new ResearchWorkspace();
})(window);
