document.addEventListener("DOMContentLoaded", function () {

    const chatBox = document.getElementById("chat-box");
    const userInput = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");

    // 自动滚动到底部
    function scrollToBottom() {
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    // 添加消息气泡
    function addMessage(role, text) {
        const bubble = document.createElement("div");
        bubble.classList.add("chat-bubble");

        if (role === "user") {
            bubble.classList.add("user-bubble");
        } else {
            bubble.classList.add("ai-bubble");
        }

        bubble.textContent = text;
        chatBox.appendChild(bubble);
        scrollToBottom();
    }

    // 添加“AI 正在思考...”动画
    function addThinkingBubble() {
        const bubble = document.createElement("div");
        bubble.classList.add("chat-bubble", "ai-bubble", "thinking");
        bubble.setAttribute("id", "thinking-bubble");
        bubble.innerHTML = `
            <span class="dot"></span>
            <span class="dot"></span>
            <span class="dot"></span>
        `;
        chatBox.appendChild(bubble);
        scrollToBottom();
    }

    function removeThinkingBubble() {
        const bubble = document.getElementById("thinking-bubble");
        if (bubble) bubble.remove();
    }

    // 点击发送
    sendBtn.addEventListener("click", sendMessage);
    userInput.addEventListener("keypress", function (e) {
        if (e.key === "Enter") {
            sendMessage();
        }
    });

    // 发送消息函数
    function sendMessage() {
        const text = userInput.value.trim();
        if (!text) return;

        addMessage("user", text);
        userInput.value = "";

        // 显示思考动画
        addThinkingBubble();

        // 发送到 Flask 后端
        fetch("/assistant/api", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ message: text })
        })
            .then(res => res.json())
            .then(data => {
                removeThinkingBubble();

                if (data.reply) {
                    addMessage("ai", data.reply);
                } else {
                    addMessage("ai", "抱歉，我暂时无法回复，请稍后再试。");
                }
            })
            .catch(err => {
                removeThinkingBubble();
                addMessage("ai", "网络错误，请检查连接或稍后重试。");
            });
    }

});
