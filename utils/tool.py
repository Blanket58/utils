import base64
import hashlib
import hmac
import itertools
import logging
import time
from operator import itemgetter
from pathlib import PurePath
from urllib.parse import quote_plus


class LoggerFactory:
    """Factory to create logger."""

    @staticmethod
    def stream(name):
        """Stream logger."""
        assert hasattr(name, '__name__') or isinstance(name, str), 'Input must be string or has attribute `__name__`.'
        if hasattr(name, '__name__'):
            name = name.__name__.upper()
        else:
            name = name.upper()
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)
            fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            formatter = logging.Formatter(fmt)
            sh = logging.StreamHandler()
            sh.setFormatter(formatter)
            logger.addHandler(sh)
        return logger

    @staticmethod
    def file(name):
        """File logger."""
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)
            fmt = '%(asctime)s - %(levelname)s - %(message)s'
            formatter = logging.Formatter(fmt)
            fh = logging.FileHandler(f'{name}.log', encoding='utf-8')
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        return logger

    @staticmethod
    def both(name):
        """Stream and file logger."""
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)
            fmt = '%(asctime)s - %(levelname)s - %(message)s'
            formatter = logging.Formatter(fmt)
            fh = logging.FileHandler(f'{name}.log', encoding='utf-8')
            fh.setFormatter(formatter)
            logger.addHandler(fh)
            sh = logging.StreamHandler()
            sh.setFormatter(formatter)
            logger.addHandler(sh)
        return logger


def balance(index, value, group, drift=100, max_iter=500, max_exchange=10000, logger=None, verbose=False):
    """
    平均分配问题，保证均分结果中每组个数与值的总和都大致相似

    Parameters
    ----------
    index: 1D array
        与值相对应的索引
    value: 1D array
        需要进行均分的值
    group: int
        均分的组数
    drift: float, optional
        允许分组结果的极差最大值
    max_iter: int, optional
        最大迭代次数
    max_exchange: int, optional
        最大交换次数
    logger: logger, optional
        日志器
    verbose: boolean
        是否打印详情

    Returns
    -------
    index : 1D array
        均分的值结果所对应的索引
    value : 1D array
        均分的值结果
    """
    import numpy as np

    if not logger:
        logger = LoggerFactory.stream(balance)

    index = index.copy()
    value = value.copy()
    remainder = value.size % group
    if remainder != 0:
        index = np.append(index, [0] * (group - remainder))
        value = np.append(value, [0] * (group - remainder))
    index = np.reshape(index, (group, -1))
    value = np.reshape(value, (group, -1))
    ncol = index.shape[1]

    diff = np.ptp(value.sum(axis=1))
    logger.info('>' * 10)
    for epoch in itertools.count(1, step=1):
        if diff <= drift:
            logger.info('Success.')
            break
        elif epoch > max_iter:
            logger.warning('Reach max iter.')
            break
        max_group_index = value.sum(axis=1).argmax()
        min_group_index = value.sum(axis=1).argmin()
        for i in range(max_exchange):
            x = np.random.randint(0, ncol)
            y = np.random.randint(0, ncol)
            index[max_group_index, x], index[min_group_index, y] = index[min_group_index, y], index[max_group_index, x]
            value[max_group_index, x], value[min_group_index, y] = value[min_group_index, y], value[max_group_index, x]
            if np.ptp(value.sum(axis=1)) < diff:
                diff = np.ptp(value.sum(axis=1))
                if verbose:
                    logger.info(f'Iter {epoch}, done after {i + 1} exchange.')
                break
        else:
            if verbose:
                logger.warning(f'Iter {epoch}, reach max exchange.')
    logger.info(f'Group number is {group}.')
    logger.info(f'Total value in each group:\n{value.sum(axis=1)}')
    logger.info(f'Total number in each group:\n{np.count_nonzero(index, axis=1)}')
    return index, value


def export_to_rule(booster):
    """
    将lightgbm写出为规则

    Parameters
    ----------
    booster : LGBMModel

    Returns
    -------
    leaf : pandas Dataframe
        The rule table.
    """
    dt = booster.booster_.trees_to_dataframe()
    categorical_columns = itemgetter(*booster.booster_.params.get('categorical_column'))(booster.feature_name_)

    def format_threshold(row):
        if row.threshold:
            if row.split_feature in categorical_columns:
                return row.threshold
            else:
                return '{:.1f}'.format(float(row.threshold))
        else:
            return None

    dt['threshold'] = dt.apply(lambda x: format_threshold(x), axis=1)
    leaf = dt.loc[dt.node_index.str.contains('L'), ['tree_index', 'node_depth', 'node_index', 'value', 'weight', 'count']]

    def get_parent_rule(node_index):
        parent_index = dt.loc[dt.node_index == node_index, 'parent_index'].values[0]
        rule = ''
        if parent_index:
            row = dt.loc[dt.node_index == parent_index]
            if row.left_child.values[0] == node_index:
                rule = (row.split_feature + row.decision_type + row.threshold).values[0]
            else:
                if row.decision_type.values[0] == '<=':
                    rule = (row.split_feature + '>' + row.threshold).values[0]
                elif row.decision_type.values[0] == '==':
                    rule = (row.split_feature + '!=' + row.threshold).values[0]
        return parent_index, rule

    def dive_rule(node_index):
        result = []
        while node_index:
            node_index, rule = get_parent_rule(node_index)
            result.append(rule)
        return ' and '.join(filter(None, result))

    leaf['rule'] = leaf.node_index.map(lambda x: dive_rule(x))
    return leaf


def dingbot(content, secret, access_token):
    import requests

    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = '{}\n{}'.format(timestamp, secret)
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(hmac_code))
    base_url = f'https://oapi.dingtalk.com/robot/send?access_token={access_token}'
    url = base_url + f'&timestamp={timestamp}&sign={sign}'
    data = {
        'msgtype': 'text',
        'text': {
            'content': content
        }
    }
    requests.post(url, json=data)


def find_all_values_in_json(data, target_key):
    if isinstance(data, dict):
        for key, value in data.items():
            if key == target_key:
                yield value
            yield from find_all_values_in_json(value, target_key)
    elif isinstance(data, list):
        for item in data:
            yield from find_all_values_in_json(item, target_key)


def pdf2png(pdf_path, png_path, zoom=200):
    """
    Convert PDF document to PNG images.

    Parameters
    ----------
    pdf_path: string
        The path of the input PDF file.
    png_path: string
        The path of the output PNG file.
    zoom: int
        The higher the value, the higher the resolution and the crisper the image quality.
    """
    import fitz

    doc = fitz.open(pdf_path)
    total = doc.page_count
    for pg in range(total):
        page = doc[pg]
        zoom = int(zoom)
        rotate = int(0)
        trans = fitz.Matrix(zoom / 100.0, zoom / 100.0).prerotate(rotate)
        pm = page.get_pixmap(matrix=trans, alpha=False)
        save = PurePath(png_path) / f'{pg+1}.png'
        pm.save(save)
    doc.close()
