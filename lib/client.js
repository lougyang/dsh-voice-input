window.__ModuleLoader__.load({
	id: "dsh-voice-input",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		var React = require("react");

		var inject = ["slots"];

		function ShutdownButton() {
			return React.createElement("button", {
				type: "button",
				title: "关闭 DeepSeek 服务",
				onClick: function () {
					if (window.confirm("确定要关闭 DeepSeek 服务吗？")) {
						fetch("/shutdown", { method: "POST" }).catch(function () {});
					}
				},
				style: {
					background: "#d93025",
					color: "#fff",
					border: "none",
					borderRadius: "6px",
					padding: "4px 12px",
					cursor: "pointer",
					fontSize: "12px"
				}
			}, "关闭服务");
		}

		function apply(ctx) {
			ctx.slots.inject("conversation.session.header.utilities", function () {
				var dispose = ctx.slots.register(
					{ name: "conversation.session.header.utilities", id: "shutdown", order: 1000 },
					ShutdownButton
				);
				return dispose;
			});
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});
