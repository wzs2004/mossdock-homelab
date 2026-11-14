(() => {
  if (document.getElementById("abs-lr-button")) return;
  const button = document.createElement("button");
  button.id = "abs-lr-button";
  button.textContent = "↗ Language Reactor";
  Object.assign(button.style, {
    position: "fixed", right: "18px", top: "105px", zIndex: "2147483500",
    background: "#167557", color: "white", border: "1px solid #65d5ac", borderRadius: "12px",
    padding: "11px 15px", font: "600 14px system-ui", cursor: "pointer", boxShadow: "0 5px 22px #0006"
  });
  document.body.appendChild(button);

  function make(tag, text, parent, css = {}) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    Object.assign(element.style, css);
    parent.appendChild(element);
    return element;
  }

  button.onclick = async () => {
    const curtain = make("div", null, document.body, {position:"fixed", inset:"0", zIndex:"2147483600", background:"#000a", display:"grid", placeItems:"center", padding:"16px"});
    const card = make("div", null, curtain, {width:"min(680px, 100%)", maxHeight:"min(85vh, 760px)", overflow:"auto", background:"#182027", color:"white", padding:"22px", borderRadius:"16px", font:"14px system-ui", boxShadow:"0 20px 60px #0009"});
    const header = make("div", null, card, {display:"flex", justifyContent:"space-between", alignItems:"center"});
    make("strong", "选择要在 Language Reactor 学习的书籍或期刊", header, {fontSize:"18px"});
    make("button", "✕", header, {color:"white", background:"transparent", border:"0", cursor:"pointer", fontSize:"20px"}).onclick = () => curtain.remove();
    curtain.addEventListener("click", event => { if (event.target === curtain) curtain.remove(); });
    const note = make("p", "正在读取已转好的音频和字幕…", card, {color:"#afc4ce"});
    const selector = make("select", null, card, {width:"100%", padding:"11px", marginBottom:"12px", background:"#273743", color:"white", border:"1px solid #50616d", borderRadius:"9px"});
    const search = make("input", null, card, {boxSizing:"border-box", width:"100%", padding:"11px", marginBottom:"12px", background:"#273743", color:"white", border:"1px solid #50616d", borderRadius:"9px"});
    search.placeholder = "搜索音轨标题或编号";
    const list = make("div", null, card, {display:"grid", gap:"5px", maxHeight:"48vh", overflowY:"auto"});
    try {
      const response = await chrome.runtime.sendMessage({type:"catalog"});
      if (!response?.ok) throw new Error(response?.error || "没有收到书库列表");
      const issues = response.data.issues;
      if (!issues.length) throw new Error("音频书库中没有文章");
      const title = document.title + " " + (document.querySelector("h1")?.textContent || "");
      const itemId = location.pathname.match(/\/item\/([^/?#]+)/)?.[1];
      let selected = itemId ? issues.findIndex(item => item.itemIds?.includes(itemId)) : -1;
      if (selected < 0) selected = issues.findIndex(item => item.name.match(/\d{4}-\d\d-\d\d/) && title.includes(item.name.match(/\d{4}-\d\d-\d\d/)[0]));
      if (selected < 0) selected = 0;
      issues.forEach((issue, index) => {
        const option = make("option", `${issue.library === "audiobooks115" ? "115 有声书 · " : "期刊 · "}${issue.name.split("/").pop()}（${issue.tracks.length} 音轨${issue.hasSubtitles ? " · 已有字幕" : ""}）`, selector);
        option.value = String(index);
      });
      selector.value = String(selected);
      const render = () => {
        const issue = issues[Number(selector.value)];
        list.replaceChildren();
        const query = search.value.trim().toLowerCase();
        note.textContent = `选中 ${issue.name}，${issue.tracks.length} 篇。点击音轨即打开 LR 并加载对应的音频 + SRT。`;
        issue.tracks.filter(track => track.name.toLowerCase().includes(query)).forEach(track => {
          const item = make("button", `${track.hasSubtitles ? "▶" : "⚠"}  ${track.name}`, list, {
            textAlign:"left", padding:"11px", borderRadius:"8px", border:"1px solid #354853", background:"#24313a", color:"white", cursor:"pointer"
          });
          item.title = track.hasSubtitles ? "打开 Language Reactor" : "这篇没有找到字幕";
          item.disabled = !track.hasSubtitles;
          if (item.disabled) item.style.opacity = ".5";
          item.onclick = async () => {
            item.disabled = true; item.textContent = "正在打开 Language Reactor…";
            const result = await chrome.runtime.sendMessage({type:"open", issueId:issue.id, index:track.index});
            if (!result?.ok) { note.textContent = `打开失败：${result?.error || "未知错误"}`; item.disabled = false; item.textContent = track.name; }
            else curtain.remove();
          };
        });
      };
      selector.onchange = render; search.oninput = render; render();
    } catch (error) {
      note.textContent = `无法读取书库：${error.message}`;
    }
  };
})();
