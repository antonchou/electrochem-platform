import { useEffect, useRef, type RefObject } from 'react';
import * as echarts from 'echarts/core';

/**
 * ECharts 实例生命周期：挂载时 init，容器或窗口尺寸变化时 resize，卸载时 dispose。
 * 返回的 ref 在挂载后指向实例；组件在本 hook 之后声明的 effect 里可直接使用它。
 * 图表类型/组件仍由各组件自行 echarts.use() 按需注册。
 */
export function useEChart(containerRef: RefObject<HTMLDivElement | null>) {
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    // 容器尺寸变化不一定伴随 window resize（弹窗滚动条、提示条挤出布局等），
    // 仅监听 window 会让 canvas 被 CSS 拉伸而模糊，故用 ResizeObserver 兜底。
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);
    return () => {
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [containerRef]);

  return chartRef;
}
