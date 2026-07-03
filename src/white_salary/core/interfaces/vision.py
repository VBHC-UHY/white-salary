"""
white_salary/core/interfaces/vision.py

Vision（计算机视觉）的抽象接口定义。

这个系统让AI能"看"东西：
  - 看屏幕截图（玩游戏时看游戏画面）
  - 看用户发来的图片
  - 看摄像头画面

看完之后，把看到的内容转成文字描述，交给LLM大脑理解。
"""

from abc import ABC, abstractmethod

from white_salary.core.interfaces.types import ImageData


class VisionInterface(ABC):
    """
    计算机视觉的抽象接口。

    所有视觉适配器都必须继承这个类。
    """

    @abstractmethod
    async def describe_image(self, image: ImageData, prompt: str = "") -> str:
        """
        看一张图片，用文字描述看到了什么。

        参数:
            image:  图片数据
            prompt: 引导提示（可选，比如"请描述这张图片中的人物"）

        返回:
            对图片内容的文字描述
        """
        ...

    @abstractmethod
    async def extract_text(self, image: ImageData) -> str:
        """
        从图片中提取文字（OCR功能）。

        比如截图中有文字，就把文字提取出来。

        参数:
            image: 图片数据

        返回:
            图片中包含的文字内容
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """
        检查视觉系统是否可用。

        返回:
            True=可用，False=不可用
        """
        ...
