(() => {
  if(window.__yktExam?.version===1)return;
  const visible=el=>!!el&&el.getClientRects().length>0&&el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const roots=()=>[...document.querySelectorAll('.exam-answer .exercise-item,.exam-main .exercise-item,[data-exam-question]')]
    .filter(visible).filter((el,i,all)=>!all.some(other=>other!==el&&other.contains(el)));
  const identity=(el,i)=>String(el.__vue__?.item?.problem_id ?? el.__vue__?.item?.id ??
    el.__vueParentComponent?.props?.item?.problem_id ?? el.dataset.questionId ?? 'exam-question-'+(i+1));
  window.__yktExam={
    version:1,
    count(){return roots().length;},
    blocked(){return [...document.querySelectorAll('.message-dialog-wrapper,.message-dialog,.screen-error-box,.el-dialog,.el-message-box,[role="dialog"]')].some(visible);},
    select(index){
      const items=roots();
      if(!Number.isInteger(index)||index<0||index>=items.length)return null;
      document.querySelectorAll('[data-ykt-exam-active]').forEach(el=>el.removeAttribute('data-ykt-exam-active'));
      const root=items[index];
      root.setAttribute('data-ykt-exam-active','1');root.dataset.yktExamIndex=String(index);
      if(!root.dataset.questionId)root.dataset.questionId=identity(root,index);
      window.__yktExamScope=true;
      const question=window.__yktHomework.read();
      if(!question)return null;
      delete question.embedded_answer;
      question.index=index+1;question.total=items.length;question.answer_source='model';
      const navigation=[...document.querySelectorAll('.exam-aside .subject-list,.dict-sheet .subject-list')]
        .map(list=>list.querySelectorAll('.subject-list-item,.subject-list__item').length);
      question.expected_total=Math.max(items.length,document.querySelectorAll('.exam-answer .exercise-item').length,...navigation);
      return question;
    },
    clear(){window.__yktExamScope=false;document.querySelectorAll('[data-ykt-exam-active]').forEach(el=>el.removeAttribute('data-ykt-exam-active'));},
    sample(){
      const all=roots();
      return {count:all.length,questions:all.slice(0,100).map((el,i)=>({index:i+1,id:identity(el,i),
        classes:el.className,type:el.querySelector('.item-type')?.innerText||'',
        controls:[...el.querySelectorAll('input,textarea,iframe,[contenteditable=true]')].map(node=>({tag:node.tagName,type:node.type||'',classes:node.className}))})),
        text:all.length?'':(document.querySelector('.exam__cover,.quiz,.exam-container,main')?.innerText||'').slice(0,5000)};
    },
  };
})();
