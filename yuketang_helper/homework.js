(() => {
  if (window.__yktHomework?.version === 12) return;
  let submissionTrack = null;
  let choiceFill = null;
  const visible = el => !!el && !el.closest('[hidden],[aria-hidden="true"]') &&
    el.getClientRects().length > 0 && (el.checkVisibility ?
      el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) : getComputedStyle(el).visibility !== 'hidden');
  const text = el => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  const normalize = value => (value || '').replace(/\s+/g, '').trim();
  const disabled = el => !el || el.disabled || el.getAttribute('aria-disabled') === 'true' ||
    !!el.closest('.is-disabled, [aria-disabled="true"]');
  const hash = value => { let h=2166136261; for (const c of value) { h^=c.charCodeAt(0); h=Math.imul(h,16777619); } return (h>>>0).toString(16); };
  const plain = html => {
    const template=document.createElement('template'); template.innerHTML=html || '';
    for (const el of template.content.querySelectorAll('script,style')) el.remove();
    for (const el of template.content.querySelectorAll('img')) el.replaceWith(document.createTextNode(
      el.getAttribute('data-latex') || el.alt || '[图片]'));
    return text(template.content);
  };
  const hasImage = html => {
    const t=document.createElement('template'); t.innerHTML=html || '';
    return [...t.content.querySelectorAll('img')].some(img => !img.hasAttribute('data-latex')) ||
      !!t.content.querySelector('audio,video,object');
  };
  const encrypted = el => !!el && (el.matches('.xuetangx-com-encrypted-font') || !!el.querySelector('.xuetangx-com-encrypted-font'));
  function metadata(root) {
    if(root.matches('[data-ykt-exam-active]')) {
      const item=root.__vue__?.item || root.__vueParentComponent?.props?.item;
      if(item && item.ProblemType!==undefined)return {
        problem_id:item.problem_id ?? item.id, index:Number(root.dataset.yktExamIndex)+1,
        content:{ProblemType:item.ProblemType,Body:item.Body,Options:item.Options},_answer:item.result,
        user:{},left_times:undefined,
      };
    }
    for (const el of [root, ...root.querySelectorAll('.problem-component,.problem-common,.homework,.subjective-problem,.subject-item')].filter(visible)) {
      const vm=el.__vue__, component=el.__vueParentComponent;
      for (let p of [vm?.problem,vm?.$props?.problem,vm?._setupProxy?.problem,
        component?.props?.problem,component?.setupState?.problem,component?.proxy?.problem]) {
        p=p?.value || p;
        if (p?.content && (p.content.Body !== undefined || p.content.ProblemType !== undefined)) return p;
      }
    }
    return null;
  }
  function current() {
    const scoped=window.__yktExamScope;
    const roots=[...document.querySelectorAll(scoped?'[data-ykt-exam-active]':'[data-question-id], .container-problem')].filter(visible);
    const root=roots.find(el => el.querySelector('.problem-body,.problem-common__body,.item-body,[data-question-stem],input[type=radio],input[type=checkbox],textarea,[contenteditable=true]'));
    if (!root) return null;
    const meta=metadata(root);
    const body=[...root.querySelectorAll('[data-question-stem],.problem-body,.problem-common__body-content,.problem-common__body'+(scoped?',.item-body > h4.exam-font,.item-body > .exam-font:first-child':''))].find(visible);
    // Read the displayed question; component props can contain hidden or stale HTML.
    const stem=body ? (body.innerText || text(body)).replace(/\s+/g,' ').trim() : meta ? plain(meta.content.Body) : '';
    const widgets=[...root.querySelectorAll('label,li,[role="radio"],[role="checkbox"],[data-option-id],.option-item,.choice-item,[class*="choice__item"],[class*="option__item"],[class*="option-item"],[class*="choice-option"]')]
      .filter(visible);
    const optionElements=[];
    const inputNodes=[...root.querySelectorAll('input[type="radio"],input[type="checkbox"]')];
    for (const input of inputNodes) {
      const el=input.closest('label,[role="radio"],[role="checkbox"],li') || input;
      if (visible(el)) optionElements.push({el,input,id:input.value,text:text(el)});
    }
    for (const el of widgets) {
      if (optionElements.some(o => o.el === el || el.contains(o.el) || o.el.contains(el))) continue;
      const id=el.dataset.optionId || el.getAttribute('data-value') || el.getAttribute('value') ||
        text(el).match(/^([A-Z])(?:[.、．\s]|$)/)?.[1];
      if (id !== undefined && id !== null) optionElements.push({el,input:null,id:String(id),text:text(el)});
    }
    const sourceOptions=Array.isArray(meta?.content?.Options) ? meta.content.Options : [];
    const options=sourceOptions.length ? sourceOptions.map(o => ({id:String(o.key),text:plain(o.value)})) :
      optionElements.map(o => ({id:o.id,text:o.text || (o.id==='true'?'正确':o.id==='false'?'错误':o.id)}));
    for (const option of options) {
      option.node=optionElements.find(o => o.id === option.id) || optionElements.find(o =>
        normalize(o.text) === normalize(option.text) || normalize(o.text) === normalize(option.id+option.text));
      // The legacy examination component renders Options in order, while its
      // labels may show A/B and its native inputs retain the default value "on".
      if(!option.node && scoped && optionElements.length===options.length)option.node=optionElements[options.indexOf(option)];
      option.textNode=option.node?.el.querySelector('.option-item__text,.radioText,.checkboxText') || option.node?.el;
      if (option.textNode && text(option.textNode)) option.text=(option.textNode.innerText || text(option.textNode)).replace(/\s+/g,' ').trim();
    }
    const blanks=[...root.querySelectorAll('.fill-blank-body input,.blank-input,input[type="text"],input:not([type])')]
      .filter(el => visible(el) && !el.closest('.edui-editor-toolbarbox,.search'));
    const area=[...root.querySelectorAll('textarea,[contenteditable="true"]')].find(visible);
    const iframe=[...root.querySelectorAll('.edui-editor-iframeholder iframe, .ueditor-wrapper iframe, .editor-container iframe')].find(visible);
    let frameBody=null;
    try { if (iframe?.contentDocument?.body?.isContentEditable) frameBody=iframe.contentDocument.body; } catch {}
    const typeCode=String(meta?.content?.ProblemType ?? root.dataset.questionType ?? '');
    const header=text(root.querySelector('.problem-common__title,.problem-header,.question-type,.item-type'));
    let kind=({'1':'single','2':'multiple','4':'blank','5':'text','6':'judgment',
      single:'single',multiple:'multiple',blank:'blank',text:'text',judgment:'judgment'})[typeCode];
    if (!kind && !typeCode) {
      if (/多选/.test(header) || inputNodes.some(el=>el.type==='checkbox')) kind='multiple';
      else if (/判断/.test(header) || (options.length===2 && options.every(o=>['true','false'].includes(o.id)))) kind='judgment';
      else if (/单选/.test(header) || inputNodes.some(el=>el.type==='radio')) kind='single';
      else if (/填空/.test(header) || blanks.length) kind='blank';
      else if (area || frameBody) kind='text';
    }
    const type=kind || 'unsupported';
    const id=String(meta?.problem_id ?? root.dataset.questionId ?? hash(stem));
    const publicOptions=options.map(({id,text})=>({id,text}));
    const fingerprint=hash(JSON.stringify([id,type,stem,publicOptions,blanks.length]));
    const inputValue=el => el?.isContentEditable ? (el.innerText || '').trim() : (el?.value || '').trim();
    const selected=o => !!o.node && (o.node.input?.checked || o.node.el.getAttribute('aria-checked')==='true' ||
      /(?:^|\s)(?:is-checked|selected|checked)(?:\s|$)/.test(o.node.el.className));
    const controls=type==='blank'?blanks:type==='text'?[area||frameBody].filter(Boolean):options.map(o=>o.node?.input||o.node?.el).filter(Boolean);
    const readOnly=meta?.left_times === 0 || (controls.length>0 && controls.every(disabled));
    const clientSelection=type==='multiple' && Array.isArray(meta?._answer)?meta._answer.map(String).sort():null;
    const selectedIds=options.filter(selected).map(o=>o.id);
    const answered=options.some(selected) || blanks.some(el=>!!el.value.trim()) || !!inputValue(area||frameBody) ||
      Number(meta?.user?.my_count || 0)>0 || !!clientSelection?.length;
    const submissionCount=Number(meta?.user?.my_count ?? root.dataset.submissionCount ?? 0);
    const submissionTime=String(meta?.user?.submit_time || root.dataset.submitTime || '');
    const feedback=[...root.querySelectorAll('.problem-answer-result,.problem-common__result,.answer-result,.problem-result')]
      .filter(visible).map(text).join(' ');
    const correctness=/回答半对|部分正确|作答半对/.test(feedback)?'partial':/回答正确|作答正确/.test(feedback)?'correct':
      /回答错误|作答错误/.test(feedback)?'incorrect':'unknown';
    const unsupported=!stem || type==='unsupported' || !controls.length ||
      (['single','multiple','judgment'].includes(type) && options.some(o=>!o.node));
    const ocrFields={stem:encrypted(body) || (!body && /xuetangx-com-encrypted-font/.test(meta?.content?.Body || '')),
      options:options.filter(o=>encrypted(o.textNode)).map(o=>o.id)};
    // The platform usually ships the key with the question data; expose it so the
    // assistant can submit it without asking a model. Text/blanks keep using AI.
    const rawKey=meta?.content?.Answer ?? meta?.content?.answer ?? meta?.Answer;
    const embeddedAnswer=rawKey===undefined || rawKey===null ? null :
      (Array.isArray(rawKey)?rawKey:[rawKey]).map(item=>String(item).trim()).filter(Boolean);
    return {root,meta,body,options,blanks,area,frameBody,iframe,selected,question:{
      id,fingerprint,type,stem,options:publicOptions,blank_count:blanks.length,answered,read_only:readOnly,
      server_identity:meta?.problem_id!==undefined || !!root.dataset.questionId,
      client_selection:clientSelection,selection_consistent:clientSelection===null || sameIds(selectedIds,clientSelection),
      index:Number(meta?.index || root.dataset.questionIndex || header.match(/^(\d+)[.．、\s]/)?.[1] ||
        questionOrder(document.querySelector('.problems-aside .subject-item.active')) || 0),
      submitted:submissionCount>0 || !!submissionTime,submission_count:submissionCount,submission_time:submissionTime,
      correctness,
      draft_fingerprint:hash(JSON.stringify([selectedIds,blanks.map(el=>el.value),inputValue(area||frameBody),
        ...(clientSelection===null?[]:[clientSelection])])),
      unsupported,has_images:meta ? hasImage(meta.content.Body) || sourceOptions.some(o=>hasImage(o.value)) :
        !!body?.querySelector('img:not([data-latex]),audio,video'),
      needs_ocr:ocrFields.stem || ocrFields.options.length>0,ocr_fields:ocrFields,document_id:performance.timeOrigin,
      embedded_answer:embeddedAnswer,
    }};
  }
  const value = el => el?.isContentEditable ? (el.innerText || '').trim() : (el?.value || '').trim();
  function setValue(el, next) {
    if (disabled(el)) throw Error('答题控件已禁用');
    if (el.isContentEditable) el.innerText=next;
    else {
      const prototype=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype,'value').set.call(el,next);
    }
    el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:next}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    el.dispatchEvent(new FocusEvent('blur',{bubbles:true}));
  }
  function editorInstance(c) {
    const instances=window.UE?.instants || window.UE?.instances || {};
    return Object.values(instances).find(editor=>editor?.iframe===c.iframe && typeof editor.setContent==='function');
  }
  const isSubmit = el => {
    if (!el) return false;
    const action=el.closest('button,a,[role="button"]') || el;
    return /(?:提交|Submit)/i.test(text(action)) ||
      !!action.closest('[data-risk-action="submit"],[data-risk-target="submit"]') ||
      action.getAttribute('type')==='submit' || (action.form && action.type==='submit');
  };
  function securityState() {
    return window.__yktVerification.read();
  }
  function dialogInfo() {
    if (securityState().required) return {state:'security-verification',element:null};
    const dialogs=[...document.querySelectorAll('.homework-problem-change-guard-dialog,[role="dialog"],.el-dialog,.el-message-box'+
      (window.__yktExamScope?',.message-dialog-wrapper,.message-dialog,.screen-error-box':''))].filter(visible);
    const guard=dialogs.find(el=>el.matches('.homework-problem-change-guard-dialog') ||
      /刚刚答(?:完)?的习题还没有提交[，,\s]*需要提交吗/.test(text(el)));
    if (guard) return {state:'unsubmitted-prompt',element:guard};
    if (dialogs.length) return {state:'blocked-dialog',element:dialogs[0]};
    return {state:'ready',element:null};
  }
  function declineButton(guard) {
    const candidates=[...guard.querySelectorAll('button,a,[role="button"],.rain-button,.rain-btn,span,div')]
      .filter(el=>visible(el) && normalize(text(el))==='先不用了' && !disabled(el) && !isSubmit(el));
    // RainBtn can render a custom div/span; bubble its ordinary DOM click.
    return candidates.find(el=>!candidates.some(child=>child!==el && el.contains(child))) || null;
  }
  function nextButton(c) {
    const bars=[...c.root.querySelectorAll('.problem-fixedbar,.question-footer,.homework-footer,.material-step-footer')].filter(visible);
    const scopes=bars.length ? bars : [document];
    const matches=[...new Set(scopes.flatMap(scope=>[...scope.querySelectorAll('button,a,[role="button"],.rain-button')]))]
      .filter(el=>visible(el) && ['下一题','Next'].includes(text(el)) && !isSubmit(el));
    const enabled=matches.filter(el=>!disabled(el) && !el.classList.contains('disable'));
    return {matches,enabled};
  }
  const questionOrder = el => Number(el?.dataset.order || text(el).match(/^\d+/)?.[0] || 0);
  const sameIds=(a,b)=>{
    const left=[...a].map(String).sort(),right=[...b].map(String).sort();
    return left.length===right.length && left.every((id,i)=>id===right[i]);
  };
  const chosen=c=>c.options.filter(c.selected).map(o=>o.id);
  const choicesMatch=(c,ids)=>sameIds(chosen(c),ids) &&
    (c.question.client_selection===null || sameIds(c.question.client_selection,ids));
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  async function flushChoice(option) {
    const el=option.node.el;
    const vm=el.__vue__ || el.__vueParentComponent?.proxy;
    if(typeof vm?.$nextTick==='function') {
      await Promise.race([new Promise(resolve=>vm.$nextTick(resolve)),delay(200)]);
    }
    await delay(0);
  }
  async function stableChoices(fingerprint,ids) {
    const until=performance.now()+1500;
    let stableAt=null,lastDraft='';
    while(performance.now()<until) {
      if(dialogInfo().state!=='ready')throw Error('网页有待处理弹窗，多选填写已暂停');
      const c=current();
      if(!c || c.question.fingerprint!==fingerprint)throw Error('题目已切换，未继续勾选旧题');
      if(choicesMatch(c,ids)) {
        if(lastDraft!==c.question.draft_fingerprint){lastDraft=c.question.draft_fingerprint;stableAt=performance.now();}
        if(stableAt!==null && performance.now()-stableAt>=60)return c;
      } else {lastDraft='';stableAt=null;}
      await delay(20);
    }
    throw Error('多选控件尚未同步完整选项，未提交答案');
  }
  async function fillMultiple({fingerprint,draft_fingerprint,answer,overwrite=false}) {
    if(dialogInfo().state!=='ready')throw Error('网页有待处理弹窗，未填写答案');
    let c=current();
    if(!c || c.question.fingerprint!==fingerprint)throw Error('题目已切换，未填写旧答案');
    if(c.question.type!=='multiple' || c.question.read_only || c.question.unsupported)throw Error('多选控件不可用');
    const ids=answer.option_ids;
    if(!Array.isArray(ids)||!ids.length||new Set(ids).size!==ids.length||ids.some(id=>!c.options.some(o=>o.id===id)))throw Error('选项编号或数量无效');
    const key=JSON.stringify([...ids].sort());
    const continuing=choiceFill?.fingerprint===fingerprint && choiceFill.initialDraft===draft_fingerprint && choiceFill.key===key;
    if(!continuing) {
      if(draft_fingerprint && c.question.draft_fingerprint!==draft_fingerprint)throw Error('等待模型期间网页答案已变化，已保留新内容');
      if(c.question.answered&&!overwrite)throw Error('本题已有答案，已保留');
      choiceFill={fingerprint,key,initialDraft:draft_fingerprint,expectedDraft:c.question.draft_fingerprint,pending:null};
    } else if(choiceFill.pending) {
      if(choicesMatch(c,choiceFill.pending.before)) {
        choiceFill.expectedDraft=c.question.draft_fingerprint;choiceFill.pending=null;
      } else {
        c=await stableChoices(fingerprint,choiceFill.pending.after);
        choiceFill.expectedDraft=c.question.draft_fingerprint;choiceFill.pending=null;
      }
    }
    if(c.question.draft_fingerprint!==choiceFill.expectedDraft)throw Error('多选填写期间答案发生其他修改，已保留当前选择');
    // Each click must reach the component model and finish rendering before the
    // next one. Re-read nodes because Vue may replace every checkbox label.
    for(let step=0;step<=c.options.length;step++) {
      if(dialogInfo().state!=='ready')throw Error('网页有待处理弹窗，多选填写已暂停');
      c=current();
      if(!c || c.question.fingerprint!==fingerprint)throw Error('题目已切换，未继续勾选旧题');
      if(c.question.draft_fingerprint!==choiceFill.expectedDraft)throw Error('多选填写期间答案发生其他修改，已保留当前选择');
      const option=c.options.find(o=>!!c.selected(o)!==ids.includes(o.id));
      if(!option){await stableChoices(fingerprint,ids);choiceFill=null;return true;}
      if(disabled(option.node.input||option.node.el))throw Error('选项已禁用');
      const before=chosen(c),after=before.filter(id=>id!==option.id);
      if(ids.includes(option.id))after.push(option.id);
      choiceFill.pending={before,after};
      option.node.el.click();
      await flushChoice(option);
      c=await stableChoices(fingerprint,after);
      choiceFill.expectedDraft=c.question.draft_fingerprint;choiceFill.pending=null;
    }
    throw Error('多选填写未完成，未提交答案');
  }
  const sidebarItem = index => index>0 ? [...document.querySelectorAll('.problems-aside .subject-item')]
    .find(el=>questionOrder(el)===index) : null;
  function baseline(c) {
    const q=c.question;
    const index=q.index || questionOrder(document.querySelector('.problems-aside .subject-item.active'));
    return {fingerprint:q.fingerprint,id:q.id,index,count:q.submission_count,time:q.submission_time,
      sidebar_submitted:!!sidebarItem(index)?.classList.contains('primary')};
  }
  function confirmed(track,c) {
    const changed = (count,time) => Number(count||0)>track.count || (!!time && String(time)!==track.time);
    if (c?.question.id===track.id && changed(c.question.submission_count,c.question.submission_time)) return true;
    if (track.meta && String(track.meta.problem_id)===track.id &&
        changed(track.meta.user?.my_count,track.meta.user?.submit_time)) return true;
    return !track.sidebar_submitted && !!sidebarItem(track.index)?.classList.contains('primary');
  }
  window.__yktHomework={
    version:12,
    read() { return current()?.question || null; },
    readAnswer(fingerprint) {
      const c=current();
      if(!c || c.question.fingerprint!==fingerprint)return null;
      if(['single','multiple','judgment'].includes(c.question.type))return {option_ids:chosen(c)};
      if(c.question.type==='blank')return {blank_answers:c.blanks.map(value)};
      if(c.question.type==='text')return {text:editorInstance(c)?.getContentTxt?.() || value(c.area||c.frameBody)};
      return null;
    },
    submissionBaseline(fingerprint) {
      const c=current();
      return c?.question.fingerprint===fingerprint ? baseline(c) : null;
    },
    resumeVerifiedSubmission({id,draft_fingerprint,confirm=false,expected_answer=null}) {
      if (securityState().required) return {state:'security-verification'};
      const c=current();
      if (!c || c.question.id!==id || c.question.draft_fingerprint!==draft_fingerprint) return {state:'draft-changed'};
      if (c.question.submitted || c.question.read_only || !c.question.answered) return {state:'not-retryable'};
      if(!c.question.selection_consistent || (expected_answer&&!this.verify({fingerprint:c.question.fingerprint,answer:expected_answer})))return {state:'answer-mismatch'};
      const info=dialogInfo();
      if (confirm && submissionTrack?.id===id && submissionTrack.confirmedCurrent &&
          info.state==='blocked-dialog' && /已作答习题未提交/.test(text(info.element)) && /仅提交本题/.test(text(info.element))) {
        submissionTrack.confirmedCurrent=false;
        return {state:'retry-confirm'};
      }
      if (info.state!=='ready') return {state:info.state};
      const bars=[...c.root.querySelectorAll('.problem-fixedbar,.question-footer,.homework-footer')].filter(visible);
      const scopes=bars.length?bars:[c.root];
      const submit=[...new Set(scopes.flatMap(el=>[...el.querySelectorAll('button,[role="button"],.rain-button,.rain-btn')]))]
        .filter(el=>visible(el)&&['提交','提交本题'].includes(text(el))&&!disabled(el)&&
          !el.classList.contains('is-loading')&&el.getAttribute('aria-busy')!=='true');
      if(submit.length!==1)return {state:'submit-not-ready'};
      submissionTrack=null;
      return {state:'retry-submit',fingerprint:c.question.fingerprint,tracking:baseline(c)};
    },
    securityState,
    ocrTargets(fingerprint) {
      const c=current();
      if (!c || c.question.fingerprint!==fingerprint) return null;
      const targets=[];
      const mark=(el,key,index)=>{
        if (!visible(el)) throw Error('没有找到可见的文字识别区域');
        const token=`${fingerprint}-${index}`;
        el.setAttribute('data-ykt-ocr',token);
        const container=el.closest('.option-item,label,li,.problem-body,.problem-common__body') || el;
        container.setAttribute('data-ykt-ocr-container',token);
        targets.push({key,selector:`[data-ykt-ocr="${token}"]`,fallback_selector:`[data-ykt-ocr-container="${token}"]`});
      };
      if (c.question.ocr_fields.stem) mark(c.body,'stem',0);
      c.options.forEach((option,i)=>{if(c.question.ocr_fields.options.includes(option.id))mark(option.textNode,'option:'+option.id,i+1);});
      return targets;
    },
    async fill({fingerprint,draft_fingerprint,answer,overwrite=false}) {
      if(current()?.question.type==='multiple')return fillMultiple({fingerprint,draft_fingerprint,answer,overwrite});
      if (dialogInfo().state!=='ready') throw Error('网页有待处理弹窗，未填写答案');
      const c=current();
      if (!c || c.question.fingerprint!==fingerprint) throw Error('题目已切换，未填写旧答案');
      if (draft_fingerprint && c.question.draft_fingerprint!==draft_fingerprint) throw Error('等待模型期间网页答案已变化，已保留新内容');
      if (c.question.read_only || c.question.unsupported) throw Error('本题只读或控件尚未支持');
      if (c.question.answered && !overwrite) throw Error('本题已有答案，已保留');
      if (['single','multiple','judgment'].includes(c.question.type)) {
        const ids=answer.option_ids;
        if (!Array.isArray(ids)||!ids.length||ids.some(id=>!c.options.some(o=>o.id===id)) ||
            new Set(ids).size!==ids.length || (c.question.type!=='multiple' && ids.length!==1)) throw Error('选项编号或数量无效');
        for (const option of c.options) {
          const wanted=ids.includes(option.id);
          if (!!c.selected(option)!==wanted && (wanted||c.question.type==='multiple')) {
            if (disabled(option.node.input||option.node.el)) throw Error('选项已禁用');
            option.node.el.click();
          }
        }
      } else if (c.question.type==='blank') {
        if (!Array.isArray(answer.blank_answers)||answer.blank_answers.length!==c.blanks.length ||
            answer.blank_answers.some(v=>typeof v!=='string'||!v.trim())) throw Error('填空答案数量无效');
        c.blanks.forEach((el,i)=>setValue(el,answer.blank_answers[i]));
      } else {
        if (typeof answer.text!=='string'||!answer.text.trim()) throw Error('简答内容为空');
        const editor=editorInstance(c);
        if (editor) {
          const escaped=answer.text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
          editor.setContent('<p>'+escaped+'</p>'); editor.fireEvent?.('contentChange'); editor.fireEvent?.('blur');
        } else if (c.area) setValue(c.area,answer.text);
        else if (c.frameBody) {
          c.frameBody.innerText=answer.text;
          c.frameBody.dispatchEvent(new c.iframe.contentWindow.InputEvent('input',{bubbles:true,inputType:'insertText',data:answer.text}));
          c.frameBody.dispatchEvent(new c.iframe.contentWindow.Event('change',{bubbles:true}));
        } else throw Error('没有找到可编辑的简答输入框');
      }
      await Promise.resolve(); await Promise.resolve();
      return this.verify({fingerprint,answer});
    },
    verify({fingerprint,answer}) {
      const c=current();
      if (!c || c.question.fingerprint!==fingerprint) return false;
      if (answer.option_ids) return choicesMatch(c,answer.option_ids);
      if (answer.blank_answers) return c.blanks.every((el,i)=>value(el)===answer.blank_answers[i].trim());
      const editor=editorInstance(c);
      const actual=editor?.getContentTxt ? editor.getContentTxt() : value(c.area||c.frameBody);
      return normalize(actual)===normalize(answer.text);
    },
    navigationState() {
      const info=dialogInfo();
      return {state:info.state,canContinue:info.state==='unsubmitted-prompt' && !!declineButton(info.element)};
    },
    submitCurrent({fingerprint,draft_fingerprint,expected_answer=null}) {
      const c=current();
      if (!c || c.question.fingerprint!==fingerprint) return {state:'question-changed'};
      const dialog=dialogInfo();
      if (dialog.state!=='ready') return {state:dialog.state};
      if (submissionTrack?.fingerprint===fingerprint && submissionTrack.requested) return {state:'already-requested'};
      if (draft_fingerprint && c.question.draft_fingerprint!==draft_fingerprint) return {state:'draft-changed'};
      if (c.question.read_only || c.question.unsupported) return {state:'not-editable'};
      if(!c.question.selection_consistent || (expected_answer&&!this.verify({fingerprint,answer:expected_answer})))return {state:'answer-mismatch'};
      const complete=c.question.type==='blank'?c.blanks.length>0&&c.blanks.every(el=>value(el)):
        c.question.type==='text'?!!(editorInstance(c)?.getContentTxt?.() || value(c.area||c.frameBody)):c.options.some(c.selected);
      if (!complete) return {state:'incomplete-answer'};
      const bars=[...c.root.querySelectorAll('.problem-fixedbar,.question-footer,.homework-footer')].filter(visible);
      const scopes=bars.length?bars:[c.root];
      const buttons=[...new Set(scopes.flatMap(el=>[...el.querySelectorAll('button,[role="button"],.rain-button,.rain-btn')]))]
        .filter(el=>visible(el)&&['提交','提交本题'].includes(text(el))&&!disabled(el));
      if (buttons.length!==1) return {state:buttons.length?'ambiguous-submit':'submit-not-ready'};
      submissionTrack={...baseline(c),requested:true,meta:c.meta,
        messages:new Map([...document.querySelectorAll('.el-message,.rain-message,[role="alert"]')].map(el=>[el,text(el)])),confirmedCurrent:false};
      buttons[0].click();
      return {state:'submit-clicked',tracking:{fingerprint,id:submissionTrack.id,index:submissionTrack.index,
        count:submissionTrack.count,time:submissionTrack.time,sidebar_submitted:submissionTrack.sidebar_submitted}};
    },
    submissionStatus(payload) {
      const fingerprint=typeof payload==='string'?payload:payload.fingerprint;
      if (securityState().required) return {state:'security-verification'};
      const c=current();
      if (!submissionTrack && payload.tracking) {
        submissionTrack={...payload.tracking,requested:true,messages:new Map(),restored:true,confirmedCurrent:false};
      }
      if (!submissionTrack || submissionTrack.fingerprint!==fingerprint) return {state:'not-requested'};
      const messages=[...document.querySelectorAll('.el-message,.rain-message,[role="alert"]')]
        .filter(el=>visible(el)&&!submissionTrack.restored&&submissionTrack.messages.get(el)!==text(el));
      if (messages.some(el=>/提交失败|提交出错|提交错误/.test(text(el)))) return {state:'submit-failed'};
      if (confirmed(submissionTrack,c)) return {state:'submitted'};
      if (messages.some(el=>/提交成功/.test(text(el)))) return {state:'submitted'};
      if (!c) return {state:'pending'};
      if (c.question.id!==submissionTrack.id) return {state:'pending'};
      const batch=[...document.querySelectorAll('.el-dialog,[role="dialog"]')].filter(visible)
        .find(el=>/已作答习题未提交/.test(text(el))&&/仅提交本题/.test(text(el)));
      if (batch) return {state:'current-submit-confirm'};
      const info=dialogInfo();
      if (info.state!=='ready') return {state:info.state};
      return {state:'pending'};
    },
    confirmCurrentSubmission(payload) {
      const fingerprint=typeof payload==='string'?payload:payload.fingerprint;
      if (securityState().required) return {state:'security-verification'};
      const c=current();
      if (!c || c.question.fingerprint!==fingerprint || submissionTrack?.fingerprint!==fingerprint) return {state:'question-changed'};
      if(!c.question.selection_consistent || (payload.expected_answer&&!this.verify({fingerprint,answer:payload.expected_answer})))return {state:'answer-mismatch'};
      if (submissionTrack.confirmedCurrent) return {state:'already-confirmed'};
      const dialog=[...document.querySelectorAll('.el-dialog,[role="dialog"]')].filter(visible)
        .find(el=>/已作答习题未提交/.test(text(el))&&/仅提交本题/.test(text(el)));
      const button=dialog && [...dialog.querySelectorAll('button,[role="button"],.rain-btn,.rain-button,span,div')]
        .find(el=>visible(el)&&normalize(text(el))==='仅提交本题'&&!disabled(el));
      if (!button) return {state:'blocked-dialog'};
      submissionTrack.confirmedCurrent=true;
      button.click();
      return {state:'confirmed-current'};
    },
    continueWithoutSubmit(fingerprint) {
      const c=current();
      if (!c || c.question.fingerprint!==fingerprint) return {state:'question-changed'};
      const info=dialogInfo();
      if (info.state!=='unsubmitted-prompt') return {state:info.state};
      const button=declineButton(info.element);
      if (!button) return {state:'blocked-dialog'};
      button.click();
      return {state:'continued-without-submit'};
    },
    navigateNext(fingerprint) {
      const c=current();
      if (!c || c.question.fingerprint!==fingerprint) return {state:'question-changed'};
      const info=dialogInfo();
      if (info.state==='unsubmitted-prompt') return this.continueWithoutSubmit(fingerprint);
      if (info.state!=='ready') return {state:info.state};
      const {matches,enabled}=nextButton(c);
      if (enabled.length>1) return {state:'ambiguous-next'};
      if (enabled.length===1) { enabled[0].click(); return {state:'clicked-next'}; }
      if (matches.length) {
        const index=c.question.index || questionOrder(document.querySelector('.problems-aside .subject-item.active'));
        const hasLater=index>0 && [...document.querySelectorAll('.problems-aside .subject-item')].some(el=>questionOrder(el)>index);
        return {state:hasLater?'next-not-ready':'end'};
      }
      return {state:'missing-next'};
    },
    next() {
      const c=current();
      if (!c) return false;
      return ['clicked-next','continued-without-submit'].includes(this.navigateNext(c.question.fingerprint).state);
    },
    openQuestions() {
      if (current()) return false;
      const el=[...document.querySelectorAll('button,a,[role="button"]')].find(el=>visible(el)&&
        ['开始作答','开始答题','查看作业','继续作答','去作答','开始做题'].includes(text(el))&&!disabled(el));
      if (!el) return false;
      el.click(); return true;
    },
    sample() {
      const c=current();
      const root=c?.root || document.querySelector('.container-problem,.homework-content,.exercise-content,.problem-content,main') || document.body;
      const copy=root.cloneNode(true);
      copy.querySelectorAll('script,style,iframe,img,audio,video,object,input[type=password],input[type=hidden],.avatar,[class*="nickname"]').forEach(el=>el.remove());
      const attributes=new Set(['class','id','type','role','name','aria-label','aria-checked','aria-disabled',
        'data-question-id','data-question-type','data-option-id','placeholder','contenteditable','disabled','checked']);
      copy.querySelectorAll('*').forEach(el=>{
        for (const attr of [...el.attributes]) {
          if (!attributes.has(attr.name)) el.removeAttribute(attr.name);
        }
        if (el.matches('textarea,[contenteditable=true]')) el.textContent='';
      });
      for (const attr of [...copy.attributes]) if (!attributes.has(attr.name)) copy.removeAttribute(attr.name);
      return {question:c?.question || null,html:copy.outerHTML.slice(0,150000)};
    },
  };
})();
