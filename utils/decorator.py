import traceback
from email.header import Header
from email.mime.text import MIMEText
from functools import wraps
from smtplib import SMTP_SSL
from time import time, localtime, strftime

from decorator import decorator

from .tool import LoggerFactory


@decorator
def retry(func, max_retry=5, logger=None, *args, **kwargs):
    """Not stop retrying until reach max limit."""
    if not logger:
        logger = LoggerFactory.stream(func)

    error = None
    for i in range(max_retry):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(e)
            logger.warning(f'Retrying [{i + 1} / {max_retry}]')
            error = e
    raise error


@decorator
def timer(func, *args, **kwargs):
    """Calculate how long the function runs."""
    logger = kwargs.get('logger')
    if not logger:
        logger = LoggerFactory.stream(func)

    start = int(round(time() * 1000))
    logger.info("Start")
    result = func(*args, **kwargs)
    end = int(round(time() * 1000)) - start
    end /= 1000
    m, s = divmod(end, 60)
    h, m = divmod(m, 60)
    logger.info("Done")
    logger.info("Total execution time: %d:%02d:%02d" % (h, m, s))
    return result


class TaskHandler:
    """
    自动任务处理器

    功能：
    1. 留日志
    2. 自动发送任务执行成功或失败邮件通知（附带报错信息）
    3. 任务计时

    Examples
    --------
    >>> th = TaskHandler('任务名', 'sender@hostname.com', '***', ['收件人1', '收件人2'])
    >>> logger = th.logger
    >>>
    >>> @th.handler
    >>> def main():
    >>>     logger.info('some info')
    >>>
    >>> if __name__ == '__main__':
    >>>     main()
    """

    def __init__(self, name, sender, password, receivers, send_success_email=False):
        """
        Parameters
        ----------

        name: str
            任务名
        sender: str
            发件人邮箱
        password: str
            发件人邮箱密码
        receivers: list[str]
            收件人邮箱列表
        send_success_email: boolean
            是否发送执行成功通知
        """
        self.task_name = name
        self.sender = sender
        self.password = password
        self.receivers = receivers
        self.send_success_email = send_success_email
        self.logger = LoggerFactory.both(name)

    def __send(self, subject, message):
        message['From'] = self.sender
        message['To'] = '; '.join(self.receivers)
        message['Subject'] = Header(subject, 'utf-8')
        smtper = SMTP_SSL('smtp.qiye.aliyun.com', 465)
        smtper.login(self.sender, self.password)
        smtper.sendmail(self.sender, self.receivers, message.as_string())
        smtper.quit()

    def __success(self, content):
        subject = f'Success -> {self.task_name}'
        message = MIMEText(f'Task {self.task_name} success.\n{content}', 'plain', 'utf-8')
        self.__send(subject, message)

    def __exception(self, content):
        subject = f'Failed -> {self.task_name}'
        message = MIMEText(content, 'plain', 'utf-8')
        self.__send(subject, message)

    def handler(self, func):
        """自动任务处理器装饰器"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            start = int(round(time() * 1000))
            try:
                self.logger.info('-' * 30)
                self.logger.info('***Start***')
                func(*args, **kwargs)
                if self.send_success_email:
                    self.__success(f'Complete at {strftime("%Y-%m-%d %H:%M:%S", localtime())}.')
            except Exception as e:
                self.logger.exception(e)
                self.__exception(str(e) + '\n' + traceback.format_exc())
                raise e
            finally:
                self.logger.info('***Exit***')
                end_ = int(round(time() * 1000)) - start
                end_ /= 1000
                m, s = divmod(end_, 60)
                h, m = divmod(m, 60)
                self.logger.info('Total execution time: %d:%02d:%02d' % (h, m, s))

        return wrapper
