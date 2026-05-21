"""同花顺交易控制模块
通过 pywinauto + UIA backend 自动化操作同花顺 v9.50.90 交易窗口
"""
import time
import logging
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError

import config

logger = logging.getLogger(__name__)


class THSTrader:
    """同花顺交易接口"""

    def __init__(self):
        self.desktop = None
        self.trade_win = None
        self._connected = False

    def connect(self):
        """连接到同花顺交易窗口"""
        try:
            self.desktop = Desktop(backend="uia")
            self.trade_win = self._find_trade_window()
            if self.trade_win:
                self._connected = True
                logger.info("已连接到同花顺交易窗口")
                return True
            else:
                logger.error("未找到交易窗口，请确保同花顺交易面板已打开")
                return False
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    def _find_trade_window(self):
        """查找交易窗口"""
        for w in self.desktop.windows():
            title = w.window_text() or ""
            if config.TRADE_WINDOW_TITLE in title:
                return w
        return None

    def _ensure_connected(self):
        """确保已连接"""
        if not self._connected or not self.trade_win:
            if not self.connect():
                raise ConnectionError("无法连接到同花顺交易窗口")

    def _click_menu(self, menu_text):
        """点击左侧菜单树中的项目"""
        self._ensure_connected()
        for child in self.trade_win.descendants():
            name = child.element_info.name or ""
            ctype = child.element_info.control_type or ""
            if menu_text in name and "TreeItem" in ctype:
                child.select()
                child.click_input()
                time.sleep(1.5)  # 等待页面加载
                logger.info(f"点击菜单: {menu_text}")
                return True
        logger.warning(f"未找到菜单项: {menu_text}")
        return False

    def _set_edit(self, automation_id, value):
        """设置输入框的值（用剪贴板粘贴，避免旧值残留）"""
        self._ensure_connected()
        import win32clipboard
        for child in self.trade_win.descendants():
            aid = child.element_info.automation_id or ""
            if aid == automation_id and child.element_info.control_type == "Edit":
                child.set_focus()
                time.sleep(0.3)
                # Ctrl+A 全选 → Delete 清空
                child.type_keys('^a')
                time.sleep(0.1)
                child.type_keys('{DELETE}')
                time.sleep(0.1)
                # 用剪贴板粘贴（比 type_keys 更可靠）
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(str(value))
                win32clipboard.CloseClipboard()
                child.type_keys('^v')
                time.sleep(0.5)
                logger.info(f"设置输入框 {automation_id} = {value}")
                return True
        logger.warning(f"未找到输入框: {automation_id}")
        return False

    def _click_button(self, automation_id):
        """点击按钮（用物理鼠标点击，UIA点击对自定义控件无效）"""
        self._ensure_connected()
        import ctypes
        user32 = ctypes.windll.user32

        for child in self.trade_win.descendants():
            aid = child.element_info.automation_id or ""
            if aid == automation_id and child.element_info.control_type == "Button":
                rect = child.element_info.rectangle
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2

                # 激活窗口
                hwnd = user32.FindWindowW(None, '网上股票交易系统5.0')
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.3)

                # 物理鼠标点击
                user32.SetCursorPos(cx, cy)
                time.sleep(0.2)
                user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                time.sleep(0.05)
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                time.sleep(0.5)
                logger.info(f"物理点击按钮: {automation_id} at ({cx}, {cy})")
                return True
        logger.warning(f"未找到按钮: {automation_id}")
        return False

    def _get_text(self, automation_id):
        """获取文本控件的值"""
        self._ensure_connected()
        for child in self.trade_win.descendants():
            aid = child.element_info.automation_id or ""
            if aid == automation_id and child.element_info.control_type == "Text":
                return child.element_info.name or ""
        return ""

    def _get_edit_value(self, automation_id):
        """获取输入框的值"""
        self._ensure_connected()
        for child in self.trade_win.descendants():
            aid = child.element_info.automation_id or ""
            if aid == automation_id and child.element_info.control_type == "Edit":
                try:
                    return child.get_value()
                except Exception:
                    return child.window_text()
        return ""

    # ========== 公开接口 ==========

    def get_balance(self):
        """获取资金信息
        先切到查询→资金股票页面，再读取资金文本
        """
        self._ensure_connected()
        try:
            # 先切到资金股票页面（资金信息只在这个页面显示）
            self._click_menu("查询[F4]")
            time.sleep(0.5)
            self._click_menu("资金股票")
            time.sleep(1.5)

            balance = {
                "资金余额": self._get_text("1012"),
                "可用金额": self._get_text("1016"),
                "总资产": self._get_text("1015"),
                "冻结金额": self._get_text("1013"),
                "股票市值": self._get_text("1014"),
                "持仓盈亏": self._get_text("1017"),
            }
            # 同时读取金额数值
            balance["_可用金额值"] = self._get_text("1016")
            balance["_总资产值"] = self._get_text("1015")
            logger.info(f"获取资金信息: 可用={balance.get('_可用金额值', '?')}")
            return balance
        except Exception as e:
            logger.error(f"获取资金信息失败: {e}")
            return {}

    def get_position(self):
        """获取持仓信息
        先点查询 → 资金股票，然后读取列表
        """
        self._ensure_connected()
        try:
            # 点击查询菜单
            self._click_menu("查询[F4]")
            time.sleep(0.5)
            # 点击资金股票子菜单
            self._click_menu("资金股票")
            time.sleep(1.5)

            # 读取持仓列表（SysListView32）
            positions = []
            for child in self.trade_win.descendants():
                ctype = child.element_info.control_type or ""
                if "ListView" in ctype:
                    try:
                        items = child.children()
                        for item in items:
                            texts = []
                            for cell in item.children():
                                name = cell.element_info.name or ""
                                texts.append(name)
                            if texts:
                                positions.append(texts)
                    except Exception:
                        pass
            logger.info(f"获取持仓: {len(positions)} 条")
            return positions
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []

    def buy(self, stock_code, quantity):
        """买入股票
        Args:
            stock_code: 股票代码，如 '600519'
            quantity: 买入数量（股），必须是100的整数倍
        Returns:
            bool: 是否成功提交委托
        """
        self._ensure_connected()
        try:
            logger.info(f"准备买入: {stock_code} x {quantity}股")

            # 确保数量是100的整数倍
            quantity = (quantity // 100) * 100
            if quantity <= 0:
                logger.error("买入数量必须大于0且为100的整数倍")
                return False

            # 点击买入菜单
            if not self._click_menu("买入[F1]"):
                return False
            time.sleep(1)

            # 输入股票代码
            if not self._set_edit("1032", stock_code):
                return False
            time.sleep(1)  # 等待代码识别，名称自动填充

            # 输入数量
            if not self._set_edit("1034", quantity):
                return False
            time.sleep(0.5)

            # 点击买入按钮
            if not self._click_button("1006"):
                return False

            # TODO: 处理确认弹窗
            time.sleep(1)

            logger.info(f"买入委托已提交: {stock_code} x {quantity}股")
            return True

        except Exception as e:
            logger.error(f"买入失败: {e}")
            return False

    def sell(self, stock_code, quantity):
        """卖出股票
        Args:
            stock_code: 股票代码
            quantity: 卖出数量（股）
        Returns:
            bool: 是否成功提交委托
        """
        self._ensure_connected()
        try:
            logger.info(f"准备卖出: {stock_code} x {quantity}股")

            # 点击卖出菜单
            if not self._click_menu("卖出[F2]"):
                return False
            time.sleep(1)

            # 输入股票代码
            if not self._set_edit("1032", stock_code):
                return False
            time.sleep(1)

            # 输入数量
            if not self._set_edit("1034", quantity):
                return False
            time.sleep(0.5)

            # 点击卖出按钮（卖出按钮也是 id=1006）
            if not self._click_button("1006"):
                return False

            time.sleep(1)
            logger.info(f"卖出委托已提交: {stock_code} x {quantity}股")
            return True

        except Exception as e:
            logger.error(f"卖出失败: {e}")
            return False

    def cancel_all(self):
        """撤消所有委托"""
        self._ensure_connected()
        try:
            if self._click_button("30001"):  # 全撤按钮
                logger.info("已撤消所有委托")
                return True
            return False
        except Exception as e:
            logger.error(f"撤单失败: {e}")
            return False


# 测试用
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    trader = THSTrader()
    if trader.connect():
        print("=== 资金信息 ===")
        balance = trader.get_balance()
        for k, v in balance.items():
            print(f"  {k}: {v}")
    else:
        print("连接失败，请确保同花顺交易面板已打开（F12）")
