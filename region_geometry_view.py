"""Multi-region native result rendering with separate physical fields."""
from pathlib import Path
import numpy as np
from PySide6 import QtCore as C,QtWidgets as W
from matplotlib.collections import PolyCollection
from region_result_view import RegionResultView


class GeometryResultView(RegionResultView):
    @staticmethod
    def attach(view,a,meta,mesh):
        view.timer.stop();view.play.setText('播放');view.data=a;view.meta=meta;view.actual_mesh=mesh;view.general=True
        view.dry=not a['pressure'].shape[1]
        if not hasattr(view,'fluid_selector'):
            view.fluid_selector=W.QComboBox();view.layout().insertWidget(1,view.fluid_selector)
            view.fluid_selector.currentIndexChanged.connect(view.draw)
        with C.QSignalBlocker(view.fluid_selector):
            view.fluid_selector.clear()
            for r in meta['case']['regions']:
                if r['interior']['kind']=='fluid':view.fluid_selector.addItem(r['id']+' · '+r['interior']['name'])
        view.fluid_selector.setVisible(not view.dry)
        from region_case import description
        c=meta['case']['model']
        view.summary.setText(description(meta['case'])+' · '+Path(meta['remote_run']).name+'\n完整地表双分量；固体速度(m/s)与各流体压力(Pa)分别显示。原生节点分介质抽样，洞内空白不插值。\n'+f"层厚/Vp/Vs/ρ={c['layers']}; f0={c['f0_hz']:g} Hz; h={c['element_m']:g} m, dt={c['dt_s']:g} s。输入改变不继承验证。")
        for w in [view.play,view.slider,view.export,view.component,view.analysis]:w.setEnabled(True)
        with C.QSignalBlocker(view.slider):view.slider.setRange(0,len(a['field_t'])-1);view.slider.setValue(len(a['field_t'])//3)
        view.draw()

    def draw(self,*_):
        if self.data is None:return
        if not getattr(self,'general',False):return super().draw(*_)
        a=self.data;c=self.meta['case']['model'];mesh=self.actual_mesh;self.figure.clear();self.axes=self.figure.subplots(2,2)
        whole,zoom,gather,pressure=self.axes.ravel();component=1 if self.component.currentText()=='Vz' else 0
        from matplotlib import colormaps
        nsolid=len(c['layers'])
        colors=colormaps['YlGn'](.22+.55*(mesh.materials-1)/max(1,nsolid-1))
        colors[mesh.materials>nsolid]=[.65,.85,1.,1.]
        whole.add_collection(PolyCollection(mesh.points[mesh.cells[:,:4]],facecolors=colors,edgecolors='#8c9898',linewidths=.15))
        top=float(mesh.points[:,1].max())+3;whole.set(xlim=(0,c['width_m']),ylim=(-sum(r[0] for r in c['layers']),top),aspect='equal',xlabel='x (m)',ylabel='z (m)')
        p=a['solid_points'];v=a['solid_velocity'][:,:,component];scale=max(float(abs(v).max()),1e-30)
        self.solid_artist=whole.scatter(p[:,0],p[:,1],c=v[0],s=2,cmap='RdBu_r',vmin=-scale,vmax=scale,zorder=2)
        self.figure.colorbar(self.solid_artist,ax=whole,label=f'Solid {self.component.currentText()} (m/s); fixed scale')
        source=self.meta['mesh_acquisition_xz_m'][0];whole.scatter(*source,marker='*',s=60,c='red',zorder=4);whole.scatter(a['x'],a['z'],s=5,c='orange',zorder=4)
        if self.dry:
            points=p;self.field_values=v;zoom.set(xlim=(0,c['width_m']),ylim=(-sum(r[0] for r in c['layers']),top))
            label=f'Solid {self.component.currentText()} (m/s)'
        else:
            index=max(0,self.fluid_selector.currentIndex());mask=a['fluid_material']==index+nsolid+1;points=a['fluid_points'][mask];self.field_values=a['fluid_pressure'][:,mask]
            r=[r for r in self.meta['case']['regions'] if r['interior']['kind']=='fluid'][index];s=r['shape']
            if s['kind']=='rectangle':x0,x1,z0,z1=s['bounds_m']
            else:
                x,z=s['center_m'];R=s['radius_m'];x0,x1,z0,z1=x-R,x+R,z-R,z+R
            zoom.set(xlim=(x0,x1),ylim=(z0,z1));scale=max(float(abs(self.field_values).max()),1e-30);label='Fluid pressure (Pa)'
            whole.scatter(*a['pressure_xy'][index],marker='x',c='blue',s=35,zorder=4)
        self.artist=zoom.scatter(points[:,0],points[:,1],c=self.field_values[0],s=2 if self.dry else 8,cmap='RdBu_r',vmin=-scale,vmax=scale)
        zoom.set(aspect='equal',xlabel='x (m)',ylabel='z (m)');self.figure.colorbar(self.artist,ax=zoom,label=label+'; fixed scale')
        val=a[self.component.currentText().lower()];clip=max(float(np.percentile(abs(val),99)),1e-30)
        gather.pcolormesh(a['x'],a['t'],val,shading='nearest',cmap='RdBu_r',vmin=-clip,vmax=clip);gather.set(xlabel='Receiver x (m)',ylabel='Time from source peak (s)',title=f'Complete {self.component.currentText()} (m/s); 99% display clip',ylim=(a['t'][-1],a['t'][0]))
        if self.dry:
            other='vx' if component else 'vz';v=a[other];clip=max(float(np.percentile(abs(v),99)),1e-30)
            pressure.pcolormesh(a['x'],a['t'],v,shading='nearest',cmap='RdBu_r',vmin=-clip,vmax=clip);pressure.set(xlabel='Receiver x (m)',ylabel='Time (s)',title=f'Complete {other} (m/s); 99% display clip',ylim=(a['t'][-1],a['t'][0]));self.pressure_line=pressure.axhline(a['field_t'][0],color='orange')
        else:
            pressure.plot(a['t'],a['pressure'][:,index]);pressure.set(xlabel='Time from source peak (s)',ylabel='Pressure (Pa)')
            from matplotlib import font_manager
            names={f.name for f in font_manager.fontManager.ttflist};family=next((f for f in ('Noto Sans CJK SC','Microsoft YaHei','Arial Unicode MS','SimHei','PingFang SC') if f in names),None)
            title=self.fluid_selector.currentText()+' | complete center record' if family else f'Fluid {index+1} | complete center record'
            pressure.set_title(title,fontfamily=family or 'DejaVu Sans');self.pressure_line=pressure.axvline(a['field_t'][0],color='orange')
        self.gather_line=gather.axhline(a['field_t'][0],color='orange');self.update_extent();self.frame(self.slider.value())
